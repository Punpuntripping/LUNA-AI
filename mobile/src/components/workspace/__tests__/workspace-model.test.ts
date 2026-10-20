/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { WorkspaceItem, WorkspaceItemKind } from "@contracts";

import {
  attachmentView,
  ATTACHMENT_TEXTS,
  cardLabel,
  groupItems,
  hiddenCount,
  isUserHidden,
  manageSpec,
  notePatch,
  ocrStatusText,
  originalFileMode,
  toggleHiddenLabel,
  visibilityActionLabel,
  visibleItems,
  wiAlias,
  workspaceMenuLabel,
} from "../workspace-model";

let seq = 0;
function item(kind: WorkspaceItemKind, over: Partial<WorkspaceItem> = {}): WorkspaceItem {
  seq += 1;
  return {
    item_id: `i${seq}`,
    user_id: "u",
    conversation_id: "c",
    case_id: null,
    wi_seq: seq,
    agent_family: null,
    kind,
    created_by: "agent",
    title: `t${seq}`,
    content_md: null,
    storage_path: null,
    document_id: null,
    is_visible: true,
    feedback: null,
    metadata: {},
    created_at: "2026-10-20T10:00:00Z",
    updated_at: "2026-10-20T10:00:00Z",
    ...over,
  };
}

describe("grouping (web WorkspaceList)", () => {
  test("web order and labels; empty groups dropped; server order kept inside a group", () => {
    const a = item("note");
    const b = item("agent_search");
    const c = item("agent_writing");
    const d = item("note");
    const e = item("convo_context");
    const groups = groupItems([a, b, c, d, e]);
    assert.deepEqual(
      groups.map((g) => [g.kind, g.label, g.items.map((i) => i.item_id)]),
      [
        ["agent_writing", "المسودات", [c.item_id]],
        ["agent_search", "نتائج البحث", [b.item_id]],
        ["note", "الملاحظات", [a.item_id, d.item_id]],
        ["convo_context", "ملخص المحادثة", [e.item_id]],
      ],
    );
  });

  test("full order: المسودات، نتائج البحث، الملاحظات، المرفقات، المراجع، ملخص المحادثة", () => {
    const kinds: WorkspaceItemKind[] = ["convo_context", "references", "attachment", "note", "agent_search", "agent_writing"];
    assert.deepEqual(
      groupItems(kinds.map((k) => item(k))).map((g) => g.label),
      ["المسودات", "نتائج البحث", "الملاحظات", "المرفقات", "المراجع", "ملخص المحادثة"],
    );
  });

  test("unknown kinds are not rendered", () => {
    const odd = item("note", { kind: "something_new" as WorkspaceItemKind });
    assert.deepEqual(groupItems([odd]), []);
  });
});

describe("card labels (web WorkspaceCard)", () => {
  test("subtype wins, unknown subtype shown raw, else kind label", () => {
    assert.equal(cardLabel(item("agent_writing", { metadata: { subtype: "memo" } })), "مذكرة");
    assert.equal(cardLabel(item("agent_writing", { metadata: { subtype: "legal_synthesis" } })), "تحليل قانوني");
    assert.equal(cardLabel(item("agent_writing", { metadata: { subtype: "brief_x" } })), "brief_x");
    assert.equal(cardLabel(item("agent_search")), "بحث");
    assert.equal(cardLabel(item("attachment", { metadata: { subtype: "" } })), "مرفق");
  });

  test("WI-n alias: Latin, only for a positive integer", () => {
    assert.equal(wiAlias(3), "WI-3");
    assert.equal(wiAlias(12), "WI-12");
    assert.equal(wiAlias(null), null);
    assert.equal(wiAlias(undefined), null);
    assert.equal(wiAlias(0), null);
    assert.equal(wiAlias(1.5), null);
  });
});

describe("hidden items", () => {
  test("user-hidden = is_visible false, except convo_context (internal by design)", () => {
    assert.equal(isUserHidden(item("note", { is_visible: false })), true);
    assert.equal(isUserHidden(item("note")), false);
    assert.equal(isUserHidden(item("convo_context", { is_visible: false })), false);
  });

  test("default list leaves hidden ones out; the toggle brings them back", () => {
    const shown = item("note");
    const hidden = item("agent_search", { is_visible: false });
    const ctx = item("convo_context", { is_visible: false });
    const all = [shown, hidden, ctx];
    assert.deepEqual(visibleItems(all, false), [shown, ctx]);
    assert.deepEqual(visibleItems(all, true), all);
    assert.equal(hiddenCount(all), 1);
  });

  test("labels", () => {
    assert.equal(toggleHiddenLabel(false, 2), "عرض العناصر المخفية (2)");
    assert.equal(toggleHiddenLabel(true, 2), "إخفاء العناصر المخفية");
    assert.equal(workspaceMenuLabel(4), "مساحة العمل (4)");
    assert.equal(workspaceMenuLabel(0), "مساحة العمل (0)");
    assert.equal(workspaceMenuLabel(null), "مساحة العمل");
    assert.equal(visibilityActionLabel(true), "إخفاء عن ريحان");
    assert.equal(visibilityActionLabel(false), "إظهار لريحان");
  });
});

describe("manage (إخفاء / حذف)", () => {
  test("never on the demo, never on convo_context; every other kind gets both", () => {
    assert.deepEqual(manageSpec("note", true), { hide: false, remove: false });
    assert.deepEqual(manageSpec("convo_context", false), { hide: false, remove: false });
    for (const k of ["note", "attachment", "agent_search", "agent_writing", "references"] as const) {
      assert.deepEqual(manageSpec(k, false), { hide: true, remove: true });
    }
  });
});

describe("note autosave patch (web MarkdownDocEditor)", () => {
  const base = { title: "ملاحظة", content: "نص" };
  test("only what changed; title trimmed", () => {
    assert.equal(notePatch(base, "ملاحظة ", "نص"), null);
    assert.deepEqual(notePatch(base, " عنوان ", "نص"), { title: "عنوان" });
    assert.deepEqual(notePatch(base, "ملاحظة", "نص جديد"), { content_md: "نص جديد" });
    assert.deepEqual(notePatch(base, "ب", "ج"), { title: "ب", content_md: "ج" });
  });
  test("a blank title skips the save", () => {
    assert.equal(notePatch(base, "   ", "نص جديد"), null);
  });
});

describe("attachment view (web AttachmentRenderer)", () => {
  test("extracted text first; toggle to the original and back", () => {
    const a = item("attachment", { content_md: "نص مستخرج", metadata: { ocr_status: "done" } });
    assert.deepEqual(attachmentView(a, false), { kind: "ocr", purged: false });
    assert.deepEqual(attachmentView(a, true), { kind: "original", canBack: true, banner: null });
  });

  test("no text yet → the original with the OCR status banner", () => {
    const pending = item("attachment", { content_md: "  " });
    assert.deepEqual(attachmentView(pending, false), {
      kind: "original",
      canBack: false,
      banner: "لم يُستخرج نص المستند بعد — يُستخرج تلقائيًا عند إرسال الرسالة",
    });
    assert.equal(ocrStatusText("empty"), "لا يحتوي المستند على نص قابل للاستخراج");
    assert.equal(ocrStatusText("failed"), "تعذّر استخراج نص هذا المستند");
    assert.equal(ocrStatusText("skipped_quota"), "تعذّر استخراج نص هذا المستند");
  });

  test("purged: text stays with no toggle; without text only the reason", () => {
    const withText = item("attachment", { content_md: "نص", metadata: { original_purged_at: "2026-10-01" } });
    assert.deepEqual(attachmentView(withText, true), { kind: "ocr", purged: true });
    const empty = item("attachment", { metadata: { original_purged_at: "x", ocr_status: "empty" } });
    assert.deepEqual(attachmentView(empty, false), { kind: "purged-empty", detail: ATTACHMENT_TEXTS.purgedEmpty });
    const failed = item("attachment", { metadata: { original_purged_at: "x", ocr_status: "failed" } });
    assert.deepEqual(attachmentView(failed, false), { kind: "purged-empty", detail: ATTACHMENT_TEXTS.purgedNoText });
  });

  test("images inline, everything else in the browser", () => {
    assert.equal(originalFileMode("image/png"), "image");
    assert.equal(originalFileMode("application/pdf"), "browser");
    assert.equal(originalFileMode(undefined), "browser");
  });
});
