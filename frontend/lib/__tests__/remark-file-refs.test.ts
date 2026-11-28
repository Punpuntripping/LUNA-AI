import assert from "node:assert/strict";
import { test } from "node:test";

import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeStringify from "rehype-stringify";

import { remarkCitations } from "../markdown/remark-citations";
import { remarkFileRefs } from "../markdown/remark-file-refs";

/**
 * The report's file-ref tags (`[F1·S7]` / `[F1]`) become `<data value="…">`
 * elements — the chain MarkdownRenderer runs (remark-gfm, citations, file refs)
 * — while library `[n]` citations stay `<cite>` (aggregator_file_refs.md §4.6, D11–D12).
 *
 * Run:  cd frontend && npx tsx --test lib/__tests__/remark-file-refs.test.ts
 */

function html(md: string): string {
  return String(
    unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkCitations)
      .use(remarkFileRefs)
      .use(remarkRehype)
      .use(rehypeStringify)
      .processSync(md),
  );
}

test("a section tag and a document tag become <data>; [n] stays a cite", () => {
  const out = html("نص «لم يقدّم» [F1·S7] ويخالف المادة 77 [3]، وانظر [F2].");
  assert.match(out, /<data value="F1\.S7">\[F1·S7\]<\/data>/);
  assert.match(out, /<data value="F2">\[F2\]<\/data>/);
  assert.match(out, /<cite data-n="3"><\/cite>/);
});

test("library groups and Arabic-Indic numbers are untouched by the file-ref plugin", () => {
  // eslint-disable-next-line no-restricted-syntax -- a library [٤] the model may write must stay a cite
  const out = html("انظر [4,17] و[٤].");
  assert.doesNotMatch(out, /<data/);
  assert.match(out, /<cite data-n="4"><\/cite><cite data-n="17"><\/cite>/);
});

test("variants map to the canonical ref", () => {
  const out = html("أ [f-1-s07] ب [F1.S9]");
  assert.match(out, /<data value="F1\.S7">\[f-1-s07\]<\/data>/);
  assert.match(out, /<data value="F1\.S9">\[F1\.S9\]<\/data>/);
});

test("the old D and WI forms are plain text", () => {
  assert.doesNotMatch(html("أ [D1·S7] ب [D2] ج [WI-1·S7] د [WI-2]"), /<data/);
});

test("one bracket with two refs: two elements, the first carries the text", () => {
  const out = html("انظر [F1·S7، F1·S9].");
  assert.match(out, /<data value="F1\.S7">\[F1·S7، F1·S9\]<\/data><data value="F1\.S9"><\/data>/);
});

test("inside emphasis, lists and table cells", () => {
  assert.match(html("**قال [F1·S2]**"), /<strong>قال <data value="F1\.S2">/);
  assert.match(html("- بند [F2·S3]"), /<li>بند <data value="F2\.S3">/);
  assert.match(html("| أ |\n|---|\n| [F1·S4] |"), /<td><data value="F1\.S4">/);
});

test("code is never touched", () => {
  const out = html("`[F1·S7]`\n\n```\n[F1·S7]\n```");
  assert.doesNotMatch(out, /<data/);
});
