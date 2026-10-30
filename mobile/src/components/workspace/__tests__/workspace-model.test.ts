/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { WI_PREVIEW_LEGACY_SUFFIX, type WorkspaceItem, type WorkspaceItemKind, type WorkspaceItemPreview } from "@contracts";

import {
  attachmentView,
  ATTACHMENT_TEXTS,
  cardLabel,
  definiteArtifactName,
  groupItems,
  hiddenCount,
  isUserHidden,
  lockedRefCount,
  manageSpec,
  notePatch,
  ocrStatusText,
  originalFileMode,
  previewAccess,
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
  test("subtype wins, else kind label", () => {
    assert.equal(cardLabel(item("agent_writing", { metadata: { subtype: "memo" } })), "مذكرة");
    assert.equal(cardLabel(item("agent_writing", { metadata: { subtype: "legal_synthesis" } })), "تحليل قانوني");
    assert.equal(cardLabel(item("agent_search")), "بحث");
    assert.equal(cardLabel(item("attachment", { metadata: { subtype: "" } })), "مرفق");
  });

  test("every writer subtype has an Arabic pill (agents/writer/models.py WriterSubtype)", () => {
    const expected: Record<string, string> = {
      contract: "عقد",
      memo: "مذكرة",
      legal_opinion: "رأي قانوني",
      defense_brief: "مذكرة دفاع",
      letter: "خطاب",
      summary: "ملخص",
      statement_of_claim: "صحيفة دعوى",
    };
    for (const [subtype, label] of Object.entries(expected)) {
      assert.equal(cardLabel(item("agent_writing", { metadata: { subtype } })), label);
    }
  });

  test("an unknown subtype falls back to the kind label, never the raw key", () => {
    assert.equal(cardLabel(item("agent_writing", { metadata: { subtype: "brief_x" } })), "مسودة");
    assert.equal(cardLabel(item("agent_search", { metadata: { subtype: "new_thing" } })), "بحث");
    assert.equal(cardLabel(item("note", { kind: "something_new" as WorkspaceItemKind, metadata: { subtype: "x_y" } })), "عنصر");
  });

  test("«افتح ال…» names (web MessageBubble): subtype, else kind, else «المصدر» — never a raw key", () => {
    assert.equal(definiteArtifactName({ kind: "agent_writing", subtype: "memo" }), "المذكرة");
    assert.equal(definiteArtifactName({ kind: "agent_writing", subtype: "defense_brief" }), "المسودة");
    assert.equal(definiteArtifactName({ kind: "agent_search", subtype: "legal_synthesis" }), "التحليل القانوني");
    assert.equal(definiteArtifactName({ kind: "agent_writing", subtype: "brief_x" }), "المسودة");
    assert.equal(definiteArtifactName({ kind: "agent_search" }), "نتيجة البحث");
    assert.equal(definiteArtifactName({ kind: "mystery" }), "المصدر");
    assert.equal(definiteArtifactName(undefined), "المصدر");
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
  test("off for every kind (not on the web; owner has not approved it)", () => {
    assert.deepEqual(manageSpec("note", true), { hide: false, remove: false });
    for (const k of ["note", "attachment", "agent_search", "agent_writing", "references", "convo_context"] as const) {
      assert.deepEqual(manageSpec(k, false), { hide: false, remove: false });
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
  test("body locked (preview): the body is never sent, a title edit still is", () => {
    assert.equal(notePatch(base, "ملاحظة", "نص جديد", true), null);
    assert.deepEqual(notePatch(base, " عنوان ", "نص جديد", true), { title: "عنوان" });
  });
});

describe("WI preview lock", () => {
  const preview = (over: Partial<WorkspaceItemPreview> = {}): WorkspaceItemPreview => ({
    locked: true,
    shown_chars: 400,
    total_chars: 1000,
    hidden_ref_count: 3,
    ...over,
  });
  const lockedSearch = () =>
    item("agent_search", { content_md: "## النتيجة\n\nالجزء الظاهر [1]" + WI_PREVIEW_LEGACY_SUFFIX, preview: preview() });

  test("no `preview` key (open item, or the server flag off): everything as before", () => {
    const open = { locked: false, editBody: true, publish: true, export: true };
    assert.deepEqual(previewAccess(item("agent_writing", { content_md: "نص" })), open);
    assert.deepEqual(previewAccess(item("agent_search", { preview: null })), open);
    assert.deepEqual(previewAccess(item("agent_search", { preview: preview({ locked: false }) })), open);
  });

  test("locked: read-only, never published or exported", () => {
    assert.deepEqual(previewAccess(lockedSearch()), { locked: true, editBody: false, publish: false, export: false });
  });

  test("hidden-sources count: the /references answer wins, else the preview block, 0 when open", () => {
    const locked = lockedSearch();
    assert.equal(lockedRefCount(locked, 5), 5);
    assert.equal(lockedRefCount(locked, undefined), 3);
    assert.equal(lockedRefCount(locked, 0), 0);
    assert.equal(lockedRefCount(item("agent_search"), 4), 0);
    assert.equal(lockedRefCount(item("agent_search", { preview: preview({ hidden_ref_count: -2 }) }), undefined), 0);
    assert.equal(lockedRefCount(item("agent_search", { preview: preview({ hidden_ref_count: Number.NaN }) }), undefined), 0);
  });

  test("attachment text is read without the legacy wall line", () => {
    const onlyLine = item("attachment", { content_md: WI_PREVIEW_LEGACY_SUFFIX, preview: preview() });
    assert.equal(attachmentView(onlyLine, false).kind, "original");
    const withText = item("attachment", { content_md: "نص" + WI_PREVIEW_LEGACY_SUFFIX, preview: preview() });
    assert.deepEqual(attachmentView(withText, false), { kind: "ocr", purged: false });
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
