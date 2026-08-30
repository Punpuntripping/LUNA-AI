import assert from "node:assert/strict";
import { test } from "node:test";

import { toLegalBlocks, type LegalImageMap } from "../legal-text";

/**
 * `toLegalBlocks`'s image handling — the half of the chunk-images feature that
 * ships BEFORE the backend and fixes something on its own.
 *
 * A regulation chunk is Arabic markdown, and 1,839 chunks carry image markup
 * pointing at a file no app can reach. The library body runs the `plain` path,
 * which parses no markdown, so until this shipped 168 published أنظمة printed
 * 1,956 of those spans as LITERAL BODY TEXT — plus 52 `seo_articles` rows in
 * their `article_text`.
 *
 * Everything below is about ONE property: the raw markup is removed whether or
 * not a map arrives. Gating it on the map is the mistake that once shipped
 * `TBL_…` reference ids to readers, and here the same mistake would restore the
 * printed filename rather than merely hide a figure.
 *
 * Run with:
 *   node --test  (after transpiling — the module under test is .tsx; the repo
 *   has no JS test runner wired up yet, so these are written framework-agnostic
 *   on `node:test` + `node:assert`.)
 */

/** The corpus's own markup, verbatim from a live chunk. */
const SPAN = "![img-1.jpeg](images/page_005_img_001.jpeg)";
/** A second one, PNG — 575 of 5,347 objects are, which is why no code appends `.jpeg`. */
const SPAN_PNG = "![img-2.png](images/page_006_img_002.png)";

test("test_a_raw_image_span_is_stripped_without_a_map", () => {
  // The stale-ISR guard. This payload is what a page baked BEFORE the backend
  // shipped `images` looks like: raw corpus markup in `text`, no map at all.
  const body = [
    "المادة الأولى",
    SPAN,
    "يقصد بالمصطلحات الآتية المعاني المبينة أمامها.",
  ].join("\n");

  const blocks = toLegalBlocks(body);
  const rendered = JSON.stringify(blocks);

  assert.ok(!rendered.includes("](images/"), "the corpus markup leaked");
  assert.ok(!rendered.includes("img-1.jpeg"), "the alt text leaked");
  assert.ok(!rendered.includes("page_005_img_001"), "the filename leaked");

  // ...and the prose around it is untouched: the emptied line flushed the
  // paragraph buffer exactly like a blank line, so the clause number above the
  // figure did not absorb the sentence below it.
  assert.deepEqual(blocks, [
    { type: "label", text: "المادة الأولى" },
    { type: "para", text: "يقصد بالمصطلحات الآتية المعاني المبينة أمامها." },
  ]);
});

test("test_an_inline_span_keeps_its_sentence", () => {
  // 47 of the 3,677 live spans sit INSIDE a prose sentence. A whole-line rule
  // would drop all 47 sentences and leave the statute looking finished.
  const blocks = toLegalBlocks(
    `يوضح الشكل ${SPAN} حدود المنطقة الصناعية.`,
  );

  assert.deepEqual(blocks, [
    { type: "para", text: "يوضح الشكل حدود المنطقة الصناعية." },
  ]);
});

test("test_a_line_that_was_only_spans_leaves_no_empty_paragraph", () => {
  const body = [
    "فقرة قبل الصور",
    "",
    `${SPAN} ${SPAN_PNG}`,
    "",
    "فقرة بعد الصور",
  ].join("\n");

  assert.deepEqual(toLegalBlocks(body), [
    { type: "para", text: "فقرة قبل الصور" },
    { type: "para", text: "فقرة بعد الصور" },
  ]);
});

test("test_an_img_token_resolves_from_the_map", () => {
  const images: LegalImageMap = {
    IMG_1: {
      n: 1,
      title: "مخطط تدفق إجراءات الترخيص",
      description:
        "مخطط انسيابي يوضح خطوات إصدار الترخيص من التقديم حتى الإصدار.",
      url: "https://dwgghvxogtwyaxmbgjod.supabase.co/storage/v1/object/public/regulation-images/17645/17645_img_1.png",
      width: 1240,
      height: 880,
    },
  };

  const blocks = toLegalBlocks(
    ["قبل الشكل", "IMG_1", "بعد الشكل"].join("\n"),
    undefined,
    images,
  );

  assert.deepEqual(blocks, [
    { type: "para", text: "قبل الشكل" },
    {
      type: "image",
      ref: "IMG_1",
      n: 1,
      title: "مخطط تدفق إجراءات الترخيص",
      description:
        "مخطط انسيابي يوضح خطوات إصدار الترخيص من التقديم حتى الإصدار.",
      url: "https://dwgghvxogtwyaxmbgjod.supabase.co/storage/v1/object/public/regulation-images/17645/17645_img_1.png",
      width: 1240,
      height: 880,
    },
    { type: "para", text: "بعد الشكل" },
  ]);
});

test("test_an_unresolved_img_token_emits_nothing", () => {
  // Rule 1's half of the same trap: a payload whose `text` came from the new
  // projector but whose `images` went missing must render a MISSING figure, not
  // a bare `IMG_3` on a statute page.
  assert.deepEqual(
    toLegalBlocks(["قبل الشكل", "IMG_3", "بعد الشكل"].join("\n")),
    [
      { type: "para", text: "قبل الشكل" },
      { type: "para", text: "بعد الشكل" },
    ],
  );
});
