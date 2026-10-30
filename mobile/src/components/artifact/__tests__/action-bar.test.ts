/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { WI_PREVIEW_LEGACY_SUFFIX, wiPreviewVisibleContent } from "@contracts";

import {
  barSlots,
  barSpec,
  buildSaveBlogRequest,
  compressionLadder,
  compressionsAt,
  hexWithAlpha,
  nextFitLevel,
  buildShareRequest,
  draftPatch,
  exportDocTitle,
  exportFileName,
  initialViewMode,
  isPreviewLockedBody,
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

describe("WI preview lock (read-only bar)", () => {
  test("an open item (no `preview`, or the flag off) keeps every control", () => {
    for (const kind of ["agent_search", "agent_writing"] as const) {
      assert.deepEqual(barSpec(kind, false, false), barSpec(kind, false));
      const spec = barSpec(kind, false);
      assert.equal(spec.publish, true);
      assert.equal(spec.export, true);
    }
  });

  test("locked agent_writing: no معاينة/تحرير, مشاركة, حفظ كمدونة or PDF — نسخ and thumbs stay", () => {
    const spec = barSpec("agent_writing", false, true);
    assert.deepEqual(spec, { toggle: false, publish: false, publishDisabled: false, export: false, feedback: true });
    assert.deepEqual(barSlots(spec), ["copy", "like", "dislike"]);
  });

  test("locked agent_search: نسخ and thumbs only", () => {
    assert.deepEqual(barSlots(barSpec("agent_search", false, true)), ["copy", "like", "dislike"]);
  });

  test("locked on the demo: نسخ alone", () => {
    assert.deepEqual(barSlots(barSpec("agent_writing", true, true)), ["copy"]);
  });

  test("export backstop: a raw locked body is refused, the visible part and open bodies are not", () => {
    const item = {
      content_md: "## التحليل\n\nنص الجزء الظاهر [1]" + WI_PREVIEW_LEGACY_SUFFIX,
      preview: { locked: true, shown_chars: 26, total_chars: 70, hidden_ref_count: 2 },
    };
    assert.equal(isPreviewLockedBody(item.content_md), true);
    assert.equal(isPreviewLockedBody(wiPreviewVisibleContent(item)), false);
    assert.equal(isPreviewLockedBody("## مذكرة\n\nنص كامل"), false);
    assert.equal(isPreviewLockedBody(""), false);
  });
});

describe("phone fit (label dropping)", () => {
  const writing = { toggle: true, share: true, saveBlog: true, export: true };

  test("agent_writing drops longest labels first, toggle last", () => {
    assert.deepEqual(compressionLadder(writing), [
      "saveBlog",
      "share",
      "copy",
      "export",
      "toggleUnselected",
      "toggleAll",
    ]);
  });

  test("only steps that change this bar", () => {
    assert.deepEqual(compressionLadder({ ...writing, toggle: false }), ["saveBlog", "share", "copy", "export"]);
    assert.deepEqual(compressionLadder({ toggle: false, share: false, saveBlog: false, export: false }), ["copy"]);
  });

  test("level 0 keeps every label; level n applies the first n steps", () => {
    const ladder = compressionLadder(writing);
    assert.equal(compressionsAt(ladder, 0).size, 0);
    assert.deepEqual([...compressionsAt(ladder, 2)], ["saveBlog", "share"]);
    assert.equal(compressionsAt(ladder, 99).size, ladder.length);
  });

  test("steps down only while overflowing, never past the ladder", () => {
    assert.equal(nextFitLevel(0, 6, 540, 344), 1);
    assert.equal(nextFitLevel(3, 6, 340, 344), 3);
    assert.equal(nextFitLevel(6, 6, 400, 344), 6);
  });

  test("hex colours get an alpha byte; others pass through", () => {
    assert.equal(hexWithAlpha("#F7F2EC", 0.95), "#F7F2ECF2");
    assert.equal(hexWithAlpha("rgba(0, 0, 0, 0.1)", 0.5), "rgba(0, 0, 0, 0.1)");
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
