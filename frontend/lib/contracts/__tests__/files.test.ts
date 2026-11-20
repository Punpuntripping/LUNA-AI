import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FILE_DOC_TYPES,
  FILE_MATCH_OPTIONS,
  FILE_TYPE_FAMILY,
  FILE_STATUS_COPY,
  FILE_TYPE_GROUP_LABELS,
  FILE_TYPE_LABELS,
  arrangeLibraryFiles,
  coerceFileDocType,
  fileGroupType,
  fileMatchQuestionParts,
  fileMatchQuestionText,
  fileStatusFromOcr,
  fileTypeChips,
  fileTypeFamily,
  fileTypePillLabel,
  filterFilesByType,
  formatFileCount,
  formatFileDate,
  formatPageCount,
  formatSectionPages,
  groupSectionsByDocument,
  libraryFileCardModel,
  normalizeArabicSearchText,
  fileMatchRequestFor,
  planMatchedUpload,
  replacedUploadCleanup,
  searchLibraryFiles,
  sortFilesNewestFirst,
  workspaceAttachmentCardModel,
  type FileDocType,
  type LibraryFile,
} from "../files";
import type { WorkspaceItem } from "../agents";

/**
 * «الملفات» — pure helpers (.claude/plans/my_files.md §3.4, §11).
 *
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 *
 * Every title / summary below is synthetic.
 */

const ARABIC_INDIC = /[٠-٩۰-۹]/;

let seq = 0;
function file(partial: Partial<LibraryFile> & { doc_type?: FileDocType | null }): LibraryFile {
  seq += 1;
  return {
    item_id: `f${seq}`,
    title: `ملف ${seq}`,
    filename: `file-${seq}.pdf`,
    mime_type: "application/pdf",
    doc_type: null,
    summary_preview: "",
    page_count: null,
    chars: 100,
    has_sections: false,
    original_available: true,
    conversation_id: "c1",
    wi_seq: seq,
    copies: 1,
    created_at: `2026-10-${String(10 + (seq % 18)).padStart(2, "0")}T10:00:00Z`,
    ...partial,
  };
}

function at(day: number, hour = 10): string {
  return `2026-10-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:00:00Z`;
}

// ---------------------------------------------------------------- labels

test("labels: every type has a pill and a group label, Arabic, Latin digits", () => {
  assert.equal(FILE_DOC_TYPES.length, 14);
  for (const type of FILE_DOC_TYPES) {
    assert.ok(FILE_TYPE_LABELS[type].length > 0, type);
    assert.ok(FILE_TYPE_GROUP_LABELS[type].length > 0, type);
    assert.doesNotMatch(FILE_TYPE_LABELS[type], /[a-z]/i);
    assert.doesNotMatch(FILE_TYPE_GROUP_LABELS[type], /[a-z]/i);
  }
});

test("group labels are the plan's §3.4 names, in its order", () => {
  assert.deepEqual(
    FILE_DOC_TYPES.map((t) => FILE_TYPE_GROUP_LABELS[t]),
    [
      "أحكام",
      "محاضر جلسات",
      "صحف دعوى ولوائح",
      "مذكرات",
      "عقود",
      "مراسلات ومحادثات",
      "خطابات وإنذارات",
      "مستندات مالية",
      "سجلات رسمية",
      "تقارير خبرة",
      "إقرارات وشهادات",
      "ملاحظاتي وجداولي",
      "مراجع ونصوص",
      "أخرى",
    ],
  );
});

test("coerceFileDocType / fileGroupType / fileTypePillLabel", () => {
  assert.equal(coerceFileDocType("judgment"), "judgment");
  assert.equal(coerceFileDocType("Judgment"), null);
  assert.equal(coerceFileDocType("court_ruling"), null);
  assert.equal(coerceFileDocType(null), null);
  assert.equal(coerceFileDocType(7), null);
  assert.equal(fileGroupType(null), "other");
  assert.equal(fileGroupType("not-a-type"), "other");
  assert.equal(fileGroupType("contract"), "contract");
  assert.equal(fileTypePillLabel("contract"), "عقد");
  assert.equal(fileTypePillLabel(null), null, "untyped → no pill");
  assert.equal(fileTypePillLabel("other"), null, "«أخرى» → no pill");
});

// ---------------------------------------------------------------- arranging

test("sortFilesNewestFirst: newest first, stable on ties and bad dates", () => {
  const a = file({ created_at: at(1) });
  const b = file({ created_at: at(5) });
  const c = file({ created_at: at(5) });
  const d = file({ created_at: "not a date" });
  assert.deepEqual(
    sortFilesNewestFirst([a, b, c, d]).map((f) => f.item_id),
    [b.item_id, c.item_id, a.item_id, d.item_id],
  );
});

test("arrange: 6 files or fewer → one flat list, newest first", () => {
  const files = [
    file({ doc_type: "judgment", created_at: at(1) }),
    file({ doc_type: "contract", created_at: at(3) }),
    file({ doc_type: "memo", created_at: at(2) }),
  ];
  const layout = arrangeLibraryFiles(files);
  assert.equal(layout.kind, "flat");
  if (layout.kind !== "flat") return;
  assert.deepEqual(layout.files.map((f) => f.doc_type), ["contract", "memo", "judgment"]);

  const six = Array.from({ length: 6 }, (_, i) =>
    file({ doc_type: i % 2 ? "judgment" : "contract", created_at: at(i + 1) }),
  );
  assert.equal(arrangeLibraryFiles(six).kind, "flat", "exactly 6 stays flat");
});

test("arrange: more than 6 files but one type → flat", () => {
  const files = Array.from({ length: 9 }, (_, i) => file({ doc_type: "letter", created_at: at(i + 1) }));
  assert.equal(arrangeLibraryFiles(files).kind, "flat");
  const untyped = Array.from({ length: 9 }, (_, i) => file({ doc_type: null, created_at: at(i + 1) }));
  assert.equal(arrangeLibraryFiles(untyped).kind, "flat", "untyped + other are one group");
});

test("arrange: more than 6 files in 2+ types → groups, largest first, «أخرى» last", () => {
  const files = [
    file({ doc_type: "judgment", created_at: at(1) }),
    file({ doc_type: "letter", created_at: at(2) }),
    file({ doc_type: "letter", created_at: at(9) }),
    file({ doc_type: "letter", created_at: at(4) }),
    file({ doc_type: "contract", created_at: at(3) }),
    file({ doc_type: "contract", created_at: at(6) }),
    file({ doc_type: null, created_at: at(7) }),
    file({ doc_type: null, created_at: at(8) }),
    file({ doc_type: "other", created_at: at(11) }),
    file({ doc_type: null, created_at: at(10) }),
    file({ doc_type: "memo", created_at: at(5) }),
  ];
  const layout = arrangeLibraryFiles(files);
  assert.equal(layout.kind, "grouped");
  if (layout.kind !== "grouped") return;
  // letter 3 · contract 2 · then judgment 1 / memo 1 by plan order · أخرى (4) last.
  assert.deepEqual(
    layout.groups.map((g) => [g.type, g.files.length]),
    [
      ["letter", 3],
      ["contract", 2],
      ["judgment", 1],
      ["memo", 1],
      ["other", 4],
    ],
  );
  assert.equal(layout.groups[0].label, "خطابات وإنذارات");
  assert.equal(layout.groups[4].label, "أخرى");
  // Newest first inside a group.
  assert.deepEqual(
    layout.groups[0].files.map((f) => f.created_at),
    [at(9), at(4), at(2)],
  );
  assert.deepEqual(
    layout.groups[4].files.map((f) => f.created_at),
    [at(11), at(10), at(8), at(7)],
  );
});

test("fileTypeChips: only the types present, counts, same order as the groups", () => {
  const files = [
    file({ doc_type: "judgment" }),
    file({ doc_type: null }),
    file({ doc_type: null }),
    file({ doc_type: "other" }),
    file({ doc_type: "letter" }),
    file({ doc_type: "letter" }),
  ];
  assert.deepEqual(fileTypeChips(files), [
    { type: "letter", label: "خطابات وإنذارات", count: 2 },
    { type: "judgment", label: "أحكام", count: 1 },
    { type: "other", label: "أخرى", count: 3 },
  ]);
  assert.deepEqual(fileTypeChips([]), []);
});

test("filterFilesByType: «الكل», a type, «أخرى» includes untyped", () => {
  const files = [file({ doc_type: "judgment" }), file({ doc_type: null }), file({ doc_type: "other" })];
  assert.equal(filterFilesByType(files, "all").length, 3);
  assert.equal(filterFilesByType(files, "judgment").length, 1);
  assert.equal(filterFilesByType(files, "other").length, 2);
  assert.equal(filterFilesByType(files, "memo").length, 0);
});

// ---------------------------------------------------------------- search

test("normalizeArabicSearchText folds what a reader does not distinguish", () => {
  assert.equal(normalizeArabicSearchText("  الإقرارُ   بالمَديونيّة "), "الاقرار بالمديونيه");
  assert.equal(normalizeArabicSearchText("آمنة"), "امنه");
  assert.equal(normalizeArabicSearchText("مستشفى"), "مستشفي");
  assert.equal(normalizeArabicSearchText("مسؤول"), "مسوول");
  assert.equal(normalizeArabicSearchText("PDF"), "pdf");
  assert.equal(normalizeArabicSearchText("قضيــــة"), "قضيه", "tatweel");
  // eslint-disable-next-line no-restricted-syntax -- the input IS Arabic-Indic on purpose
  assert.equal(normalizeArabicSearchText("رقم ١٢٣"), "رقم 123");
});

test("searchLibraryFiles: title, summary preview and file name; every word must match", () => {
  const a = file({ title: "عقد إيجار تجاري", summary_preview: "", filename: "lease.pdf" });
  const b = file({ title: "مذكرة", summary_preview: "مطالبة بأجرة متأخرة عن محل تجاري", filename: "x.pdf" });
  const c = file({ title: "صورة", summary_preview: "", filename: "scan-2026.jpg" });
  const all = [a, b, c];
  assert.deepEqual(searchLibraryFiles(all, "").map((f) => f.item_id), all.map((f) => f.item_id));
  assert.deepEqual(searchLibraryFiles(all, "   ").length, 3);
  assert.deepEqual(searchLibraryFiles(all, "تجاري").map((f) => f.item_id), [a.item_id, b.item_id]);
  assert.deepEqual(searchLibraryFiles(all, "ايجار").map((f) => f.item_id), [a.item_id], "hamza folded");
  assert.deepEqual(searchLibraryFiles(all, "تجاري أجرة").map((f) => f.item_id), [b.item_id], "AND");
  assert.deepEqual(searchLibraryFiles(all, "SCAN").map((f) => f.item_id), [c.item_id], "file name, case");
  assert.deepEqual(searchLibraryFiles(all, "غير موجود"), []);
  // Null-ish fields do not throw.
  assert.equal(searchLibraryFiles([{ title: "عنوان", summary_preview: null, filename: null }], "عنوان").length, 1);
});

// ---------------------------------------------------------------- labels with numbers

test("formatPageCount: Arabic agreement, Latin digits", () => {
  assert.equal(formatPageCount(null), "");
  assert.equal(formatPageCount(0), "");
  assert.equal(formatPageCount(1), "صفحة واحدة");
  assert.equal(formatPageCount(2), "صفحتان");
  assert.equal(formatPageCount(3), "3 صفحات");
  assert.equal(formatPageCount(10), "10 صفحات");
  assert.equal(formatPageCount(11), "11 صفحة");
  assert.equal(formatPageCount(99), "99 صفحة");
  assert.equal(formatPageCount(100), "100 صفحة");
  assert.equal(formatPageCount(102), "102 صفحة");
  assert.equal(formatPageCount(103), "103 صفحات", "n%100 in 3–10 → plural");
  assert.equal(formatPageCount(318), "318 صفحة");
  for (const n of [1, 2, 3, 11, 100]) assert.doesNotMatch(formatPageCount(n), ARABIC_INDIC);
});

test("formatFileCount: 1 / 2 / 3–10 / 11–99 / 100+", () => {
  assert.equal(formatFileCount(0), "");
  assert.equal(formatFileCount(1), "ملف واحد");
  assert.equal(formatFileCount(2), "ملفان");
  assert.equal(formatFileCount(3), "3 ملفات");
  assert.equal(formatFileCount(10), "10 ملفات");
  assert.equal(formatFileCount(11), "11 ملفًا");
  assert.equal(formatFileCount(120), "120 ملفًا");
  assert.equal(formatFileCount(100), "100 ملف");
  assert.equal(formatFileCount(205), "205 ملفات");
  for (const n of [1, 2, 3, 11, 120, 500]) assert.doesNotMatch(formatFileCount(n), ARABIC_INDIC);
});

test("type families: every type in one of five, untyped → general", () => {
  assert.equal(Object.keys(FILE_TYPE_FAMILY).length, 14);
  assert.deepEqual(
    [...new Set(Object.values(FILE_TYPE_FAMILY))].sort(),
    ["correspondence", "court", "general", "money", "records"],
  );
  assert.equal(fileTypeFamily("judgment"), "court");
  assert.equal(fileTypeFamily("contract"), "records");
  assert.equal(fileTypeFamily("financial"), "money");
  assert.equal(fileTypeFamily("letter"), "correspondence");
  assert.equal(fileTypeFamily(null), "general");
  assert.equal(fileTypeFamily("not-a-type"), "general");
});

test("formatSectionPages", () => {
  assert.equal(formatSectionPages(null, null), "");
  assert.equal(formatSectionPages(3, 5), "ص 3–5");
  assert.equal(formatSectionPages(4, 4), "ص 4");
  assert.equal(formatSectionPages(4, null), "ص 4");
  assert.equal(formatSectionPages(null, 9), "ص 9");
  assert.equal(formatSectionPages(7, 2), "ص 2–7");
});

test("formatFileDate: اليوم / أمس / full date with Latin digits", () => {
  const now = new Date(2026, 10, 19, 15, 0);
  assert.equal(formatFileDate(new Date(2026, 10, 19, 1, 0).toISOString(), now), "اليوم");
  assert.equal(formatFileDate(new Date(2026, 10, 18, 23, 0).toISOString(), now), "أمس");
  const full = formatFileDate(new Date(2026, 9, 31, 12, 0).toISOString(), now);
  assert.match(full, /31/);
  assert.match(full, /2026/);
  assert.doesNotMatch(full, ARABIC_INDIC);
  assert.equal(formatFileDate("nope", now), "");
});

// ---------------------------------------------------------------- card model

test("fileStatusFromOcr", () => {
  assert.equal(fileStatusFromOcr(undefined, true), "ready");
  assert.equal(fileStatusFromOcr("failed", true), "ready", "text wins");
  assert.equal(fileStatusFromOcr(undefined, false), "pending", "not sent yet");
  assert.equal(fileStatusFromOcr(undefined, false, true), "processing", "send is extracting");
  assert.equal(fileStatusFromOcr("failed", false, true), "failed");
  assert.equal(fileStatusFromOcr("empty", false), "empty");
  assert.equal(fileStatusFromOcr("failed", false), "failed");
  assert.equal(fileStatusFromOcr("skipped_quota", false), "failed");
  assert.equal(FILE_STATUS_COPY.processing, "جارٍ استخراج النص…");
  assert.equal(FILE_STATUS_COPY.pending, "لم يُستخرج النص بعد — يُستخرج عند إرسال الرسالة");
  assert.equal(FILE_STATUS_COPY.empty, "لا يحتوي على نص قابل للاستخراج");
  assert.equal(FILE_STATUS_COPY.failed, "تعذّر استخراج النص");
});

test("libraryFileCardModel / workspaceAttachmentCardModel", () => {
  const lf = file({ doc_type: "memo", summary_preview: "سطران", page_count: 4, wi_seq: 3 });
  assert.deepEqual(libraryFileCardModel(lf), {
    item_id: lf.item_id,
    title: lf.title,
    doc_type: "memo",
    summary_preview: "سطران",
    page_count: 4,
    created_at: lf.created_at,
    wi_seq: 3,
    status: "ready",
    reattach_advised: false,
  });
  // «ننصح بإعادة إرفاق هذا الملف»: only an explicit `true` from the server turns it on.
  assert.equal(libraryFileCardModel({ ...lf, reattach_advised: true }).reattach_advised, true);

  const base: WorkspaceItem = {
    item_id: "w1",
    user_id: "u1",
    conversation_id: "c1",
    case_id: null,
    wi_seq: 2,
    agent_family: null,
    kind: "attachment",
    created_by: "user",
    title: "مرفق",
    content_md: "",
    storage_path: "p",
    document_id: null,
    is_visible: true,
    feedback: null,
    metadata: {},
    created_at: at(3),
    updated_at: at(3),
  };
  const pending = workspaceAttachmentCardModel(base);
  assert.equal(pending.status, "pending");
  assert.equal(pending.reattach_advised, false);
  assert.equal(workspaceAttachmentCardModel({ ...base, reattach_advised: true }).reattach_advised, true);
  assert.equal(workspaceAttachmentCardModel({ ...base, reattach_advised: null }).reattach_advised, false);
  assert.equal(workspaceAttachmentCardModel(base, { extracting: true }).status, "processing");
  assert.equal(pending.summary_preview, "");
  assert.equal(pending.doc_type, null);

  const done = workspaceAttachmentCardModel({
    ...base,
    content_md: "نص",
    summary_preview: "ملخص",
    metadata: { doc_type: "letter", ocr_status: "done", page_count: 2, ocr_page_total: 5 },
  });
  assert.equal(done.status, "ready");
  assert.equal(done.doc_type, "letter");
  assert.equal(done.page_count, 5);
  assert.equal(done.summary_preview, "ملخص");

  const failed = workspaceAttachmentCardModel({ ...base, metadata: { ocr_status: "failed", ocr_pages: 3 } });
  assert.equal(failed.status, "failed");
  assert.equal(failed.page_count, 3);
});

// ---------------------------------------------------------------- sections

test("groupSectionsByDocument: one heading per document, only with 2+ documents", () => {
  const s = (seqN: number, document_index: number | null) => ({ seq: seqN, document_index });
  const one = groupSectionsByDocument([s(1, null), s(2, null)], []);
  assert.equal(one.length, 1);
  assert.equal(one[0].heading, null);
  assert.equal(one[0].sections.length, 2);

  // Fewer than 2 documents → no headings even if indexes are present.
  assert.equal(groupSectionsByDocument([s(1, 0)], [{ name: "أ" }])[0].heading, null);

  const runs = groupSectionsByDocument(
    [s(1, 0), s(2, 0), s(3, 1), s(4, null), s(5, 1)],
    [{ name: "عقد البيع" }, { name: "  " }],
  );
  assert.deepEqual(
    runs.map((r) => [r.heading, r.sections.map((x) => x.seq)]),
    [
      ["عقد البيع", [1, 2]],
      ["المستند 2", [3]],
      [null, [4]],
      ["المستند 2", [5]],
    ],
  );
});

// ---------------------------------------------------------------- approval popup

test("same-file approval: two options, no «أخرى», the question text", () => {
  assert.deepEqual(
    FILE_MATCH_OPTIONS.map((o) => o.label),
    ["استخدام الموجود", "رفع نسخة جديدة"],
  );
  assert.equal(FILE_MATCH_OPTIONS[0].value, "use_existing");
  assert.equal(FILE_MATCH_OPTIONS[1].value, "upload_new");
  assert.ok(FILE_MATCH_OPTIONS[0].description);

  const other = { title: "عقد", created_at: new Date(2026, 9, 31, 12).toISOString(), same_conversation: false, wi_seq: 4 };
  const parts = fileMatchQuestionParts(other);
  assert.equal(parts.wiSeq, null, "WI only for a same-conversation match");
  const text = fileMatchQuestionText(other);
  assert.match(text, /^وجدنا هذا الملف ضمن ملفاتك: «عقد» · .*2026 — هل نستخدمه؟$/);
  assert.doesNotMatch(text, ARABIC_INDIC);

  const same = fileMatchQuestionText({ ...other, same_conversation: true });
  assert.match(same, /«عقد» \(WI-4\) · /);

  const noDate = fileMatchQuestionText({ ...other, created_at: "?" });
  assert.equal(noDate, "وجدنا هذا الملف ضمن ملفاتك: «عقد» — هل نستخدمه؟");
});

// ---------------------------------------------------------------- attach flow (§3.8)

test("fileMatchRequestFor: the init values + conversation; nothing to ask without a conversation", () => {
  const file = { name: "scan.pdf", size: 1234, type: "application/pdf" };
  assert.deepEqual(fileMatchRequestFor(file, "c1"), {
    filename: "scan.pdf",
    size_bytes: 1234,
    mime_type: "application/pdf",
    conversation_id: "c1",
  });
  assert.equal(fileMatchRequestFor(file, null), null, "brand-new chat: no id yet");
  assert.equal(fileMatchRequestFor(file, ""), null);
  assert.equal(fileMatchRequestFor({ ...file, type: "" }, "c1"), null);
  assert.equal(fileMatchRequestFor({ ...file, name: "" }, "c1"), null);
});

const MATCH = { item_id: "old", same_conversation: true };

test("planMatchedUpload: nothing changes without approval", () => {
  const uploading = { status: "uploading" as const, itemId: "new" };
  const keep = { useExisting: false, cleanup: "none" };
  assert.deepEqual(planMatchedUpload({ match: null, answer: "use_existing", upload: uploading }), keep);
  assert.deepEqual(planMatchedUpload({ match: MATCH, answer: null, upload: uploading }), keep, "closed / sent first");
  assert.deepEqual(planMatchedUpload({ match: MATCH, answer: "upload_new", upload: uploading }), keep);
  assert.deepEqual(
    planMatchedUpload({ match: { ...MATCH, same_conversation: false }, answer: "use_existing", upload: uploading }),
    keep,
    "Phase 1b: only a copy in this conversation can be attached",
  );
  assert.deepEqual(
    planMatchedUpload({ match: { ...MATCH, item_id: "new" }, answer: "use_existing", upload: uploading }),
    keep,
    "the match is the new upload itself",
  );
});

test("planMatchedUpload: «استخدام الموجود» cleans the new upload by its state", () => {
  const plan = (status: "queued" | "uploading" | "completed" | "failed" | "cancelled", itemId: string | null) =>
    planMatchedUpload({ match: MATCH, answer: "use_existing", upload: { status, itemId } });
  assert.deepEqual(plan("queued", null), { useExisting: true, cleanup: "cancel_upload_once_started" });
  assert.deepEqual(plan("uploading", null), { useExisting: true, cleanup: "cancel_upload_once_started" });
  assert.deepEqual(plan("uploading", "new"), { useExisting: true, cleanup: "cancel_upload" });
  assert.deepEqual(plan("completed", "new"), { useExisting: true, cleanup: "delete_new_item" });
  assert.deepEqual(plan("completed", null), { useExisting: true, cleanup: "none" });
  assert.deepEqual(plan("failed", "new"), { useExisting: true, cleanup: "none" });
  assert.deepEqual(plan("cancelled", null), { useExisting: true, cleanup: "none" });
});

test("replacedUploadCleanup: late events of an upload already replaced", () => {
  assert.equal(replacedUploadCleanup("started"), "cancel_upload");
  assert.equal(replacedUploadCleanup("completed"), "delete_new_item");
  assert.equal(replacedUploadCleanup("failed"), "none");
  assert.equal(replacedUploadCleanup("cancelled"), "none");
});
