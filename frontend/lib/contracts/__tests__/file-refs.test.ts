import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FILE_REFS_AR,
  canonicalFileRef,
  fileRefTextLabel,
  fileRefsToText,
  findFileRefTags,
  formatFileRef,
  orderFileRefs,
  parseFileRef,
  stripFileRefTags,
  type FileRefRow,
} from "../file-refs";
import { appendReferencesForCopy } from "../references-copy";
import { CITATION_RE } from "../citations";

/**
 * «من مستنداتك» — the report's `[F1·S7]` / `[F1]` file-ref grammar and text
 * forms (.claude/plans/aggregator_file_refs.md §4.1, §4.6, §4.7, D11–D12: F + the
 * file's WI number). The same
 * cases run against the Python grammar in shared/workspace/tests/test_file_refs.py.
 *
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

function row(ref: string, over: Partial<FileRefRow> = {}): FileRefRow {
  const parsed = parseFileRef(ref)!;
  return {
    n: 1,
    ref_id: ref,
    wi_seq: parsed.wiSeq,
    section_seq: parsed.section,
    page_from: null,
    page_to: null,
    doc_title: "الحكم المرفق",
    what: "أسباب الحكم",
    owner: "",
    brought_by: "",
    available: true,
    ...over,
  };
}

test("canonicalFileRef: the accepted spellings", () => {
  for (const v of [
    "F1·S7", "F1.S7", "F-1·S7", "f1.s07", "f-1-s07", "F 1 S7", "[F1S7]", "F01·S007",
    "F1:S7", "F1_S7", "F1/S7", "F1، S7", "F_1·S7", "[ F1 · S7 ]",
  ]) {
    assert.equal(canonicalFileRef(v), "F1.S7", v);
  }
  // eslint-disable-next-line no-restricted-syntax -- parser input: the model may write Arabic-Indic digits
  assert.equal(canonicalFileRef("F١·S٧"), "F1.S7");
  // eslint-disable-next-line no-restricted-syntax -- parser input: Extended Arabic-Indic digits too
  assert.equal(canonicalFileRef("F۳·S۷"), "F3.S7");
  assert.equal(canonicalFileRef("[F2]"), "F2");
  assert.equal(canonicalFileRef("f2"), "F2");
  assert.equal(canonicalFileRef("F12"), "F12");
});

test("canonicalFileRef: not a file ref (the old D and WI forms included)", () => {
  for (const v of [
    "4", "[4]", "S7", "F0·S7", "F1·S0", "", "D1·S7", "D2", "F1·S", "FF1", "F",
    "WI-1·S7", "WI-1", "[PDF 3]",
  ]) {
    assert.equal(canonicalFileRef(v), null, v);
  }
});

test("parseFileRef / formatFileRef round-trip", () => {
  assert.deepEqual(parseFileRef("F2·S7"), { wiSeq: 2, section: 7 });
  assert.deepEqual(parseFileRef("F3"), { wiSeq: 3, section: null });
  assert.equal(formatFileRef(2, 7), "F2.S7");
  assert.equal(formatFileRef(3, null), "F3");
});

test("findFileRefTags: tags in text, library [n] never matches", () => {
  // eslint-disable-next-line no-restricted-syntax -- a library [٤] the model may write must not match
  const text = "نصّ «لم يقدّم ما يثبت السداد» [F1·S7]، ويخالف المادة 77 [3] و[4,17] و[٤] و[F2].";
  const tags = findFileRefTags(text);
  assert.deepEqual(tags.map((t) => t.refs), [["F1.S7"], ["F2"]]);
  assert.equal(text.slice(tags[0].start, tags[0].end), "[F1·S7]");
  assert.equal(tags[1].raw, "[F2]");
});

test("findFileRefTags: several refs in one bracket, repeats dropped", () => {
  const [tag] = findFileRefTags("انظر [F1·S7، F1·S9, f1-s07]");
  assert.deepEqual(tag.refs, ["F1.S7", "F1.S9"]);
  const [two] = findFileRefTags("انظر [F1، F2]");
  assert.deepEqual(two.refs, ["F1", "F2"]);
});

test("findFileRefTags: not tags (the old D and WI forms included)", () => {
  assert.deepEqual(
    findFileRefTags("[F1·S7 — Ali] [F] [S7] [D1·S7] [D2] [WI-1·S7] [WI-2] [abc] [PDF 3] F3"),
    [],
  );
});

test("the library citation regex never matches a file tag", () => {
  const re = new RegExp(CITATION_RE.source, CITATION_RE.flags);
  assert.equal(re.exec("[F1·S7] [F2] [f-1-s07]"), null);
});

test("fileRefTextLabel: section and whole document", () => {
  assert.equal(fileRefTextLabel(row("F1.S7")), "(القسم 7 من الحكم المرفق)");
  assert.equal(fileRefTextLabel(row("F2")), "(الحكم المرفق)");
  assert.equal(fileRefTextLabel(row("F2", { doc_title: "" })), "(المستند المرفق)");
});

test("fileRefsToText: known → label, unknown → removed with its spaces, undefined → unchanged", () => {
  const body = "قال الحكم «لم يقدّم» [F1·S7]، ثم [F9·S1] انتهى [3].";
  const rows = [row("F1.S7")];
  assert.equal(
    fileRefsToText(body, rows),
    "قال الحكم «لم يقدّم» (القسم 7 من الحكم المرفق)، ثم انتهى [3].",
  );
  assert.equal(fileRefsToText(body, undefined), body);
});

test("fileRefsToText: side-by-side tags stay two labels", () => {
  const rows = [row("F1.S7"), row("F1.S9", { n: 2 })];
  assert.equal(
    fileRefsToText("نص [F1·S7][F1·S9].", rows),
    "نص (القسم 7 من الحكم المرفق) (القسم 9 من الحكم المرفق).",
  );
});

test("stripFileRefTags: every tag dropped, library refs kept", () => {
  assert.equal(stripFileRefTags("أ [F1·S7] ب [F2] ج [4]"), "أ ب ج [4]");
  assert.equal(stripFileRefTags("بلا وسوم [4]"), "بلا وسوم [4]");
});

test("appendReferencesForCopy: file refs as text; omitted = body as is", () => {
  const body = "نص [F1·S7] [1]";
  assert.equal(appendReferencesForCopy(body, []), body);
  assert.equal(appendReferencesForCopy(body, [], [row("F1.S7")]), "نص (القسم 7 من الحكم المرفق) [1]");
  assert.equal(appendReferencesForCopy(body, [], []), "نص [1]");
});

test("orderFileRefs: n order", () => {
  const ordered = orderFileRefs([row("F2", { n: 3 }), row("F1.S7", { n: 1 }), row("F1.S9", { n: 2 })]);
  assert.deepEqual(ordered.map((r) => r.ref_id), ["F1.S7", "F1.S9", "F2"]);
});

test("FILE_REFS_AR.chipLabel", () => {
  assert.equal(FILE_REFS_AR.chipLabel(row("F1.S7")), "القسم 7 من الحكم المرفق");
  assert.equal(FILE_REFS_AR.chipLabel(row("F2")), "الحكم المرفق");
});
