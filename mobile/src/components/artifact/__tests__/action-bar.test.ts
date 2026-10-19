/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  barSlots,
  barSpec,
  buildSaveBlogRequest,
  buildShareRequest,
  draftPatch,
  exportDocTitle,
  exportFileName,
  initialViewMode,
  nextFeedback,
  sanitizeExportFilename,
  saveBlogDefaultTitle,
  SAVE_BLOG_ERRORS,
  SHARE_ERRORS,
  shouldAdoptServer,
} from "../action-bar";

describe("bar layout (web WorkspaceItemActionBar order)", () => {
  test("agent_writing: toggle first, then publish, copy, export, thumbs", () => {
    assert.deepEqual(barSlots(barSpec("agent_writing", false)), [
      "mode",
      "share",
      "saveBlog",
      "copy",
      "export",
      "like",
      "dislike",
    ]);
  });

  test("agent_search: no toggle (read-only)", () => {
    assert.deepEqual(barSlots(barSpec("agent_search", false)), ["share", "saveBlog", "copy", "export", "like", "dislike"]);
  });

  test("demo: thumbs hidden, publish rendered but disabled", () => {
    const spec = barSpec("agent_search", true);
    assert.equal(spec.publishDisabled, true);
    assert.equal(spec.feedback, false);
    assert.deepEqual(barSlots(spec), ["share", "saveBlog", "copy", "export"]);
  });
});

describe("nextFeedback", () => {
  test("tapping the active thumb clears it", () => {
    assert.equal(nextFeedback("up", "up"), null);
    assert.equal(nextFeedback("down", "down"), null);
  });
  test("tapping the other thumb or a fresh one sets it", () => {
    assert.equal(nextFeedback(null, "up"), "up");
    assert.equal(nextFeedback("up", "down"), "down");
    assert.equal(nextFeedback("down", "up"), "up");
  });
});

describe("share requests", () => {
  test("question mode trims and sends a null title", () => {
    assert.deepEqual(buildShareRequest("question", "  ما حكم الفسخ؟ ", "ignored"), {
      ok: true,
      body: { question_text: "ما حكم الفسخ؟", display_mode: "question", title: null },
    });
  });
  test("title mode sends an empty question", () => {
    assert.deepEqual(buildShareRequest("title", "ignored", " فسخ العقود "), {
      ok: true,
      body: { question_text: "", display_mode: "title", title: "فسخ العقود" },
    });
  });
  test("blank fields are refused in Arabic", () => {
    assert.deepEqual(buildShareRequest("question", "   ", "x"), { ok: false, error: SHARE_ERRORS.emptyQuestion });
    assert.deepEqual(buildShareRequest("title", "x", ""), { ok: false, error: SHARE_ERRORS.emptyTitle });
    assert.deepEqual(buildSaveBlogRequest(" "), { ok: false, error: SAVE_BLOG_ERRORS.emptyTitle });
  });
  test("save-as-blog is a title-mode publish", () => {
    assert.deepEqual(buildSaveBlogRequest("مقال"), {
      ok: true,
      body: { question_text: "", display_mode: "title", title: "مقال" },
    });
  });
  test("save-as-blog title falls back to the question", () => {
    assert.equal(saveBlogDefaultTitle({ default_title: " عنوان ", default_question: "س" }), "عنوان");
    assert.equal(saveBlogDefaultTitle({ default_title: null, default_question: "سؤال" }), "سؤال");
    assert.equal(saveBlogDefaultTitle({ default_title: "  ", default_question: "" }), "");
  });
});

describe("export file names", () => {
  test("illegal characters become spaces and runs collapse", () => {
    assert.equal(sanitizeExportFilename('مذكرة: "دفاع" / الجلسة 2'), "مذكرة دفاع الجلسة 2");
    assert.equal(exportFileName("تحليل", "pdf"), "تحليل.pdf");
    assert.equal(exportFileName("تحليل", "docx"), "تحليل.docx");
  });
  test("blank → «مستند»; long titles are cut", () => {
    assert.equal(sanitizeExportFilename(" ?* "), "مستند");
    assert.equal(sanitizeExportFilename("ا".repeat(300)).length, 120);
    assert.equal(exportDocTitle("ب".repeat(300)).length, 200);
    assert.equal(exportDocTitle("  "), "مستند");
  });
});

describe("draft autosave rules", () => {
  test("opens in preview with content, edit when empty", () => {
    assert.equal(initialViewMode("# نص"), "preview");
    assert.equal(initialViewMode("  \n"), "edit");
  });
  test("patch only what changed", () => {
    assert.equal(draftPatch("a", "a"), null);
    assert.deepEqual(draftPatch("a", "ab"), { content_md: "ab" });
  });
  test("adopt outside edits only when nothing local is unsaved", () => {
    // Agent wrote more while the user had not typed.
    assert.equal(shouldAdoptServer("v2", "v1", "v1"), true);
    // The echo of our own save.
    assert.equal(shouldAdoptServer("v1", "v1", "v1"), false);
    // The user has unsaved keystrokes — never overwrite them.
    assert.equal(shouldAdoptServer("v2", "v1-typing", "v1"), false);
  });
});
