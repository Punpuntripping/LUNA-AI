import type { WorkspaceItem, WorkspaceItemKind } from "@contracts";

// Pure rules of the workspace screen — a port of the web's chat-pane
// workspace (frontend/components/workspace/WorkspaceList, WorkspaceCard,
// WiBadge, AttachmentRenderer, ConvoContextViewer, ReferencesRenderer,
// WorkspaceAddMenu, NoteEditor). No React Native here so `npm test` covers it.

// --- list --------------------------------------------------------------------

/** Group headings — web WorkspaceList `KIND_LABELS`. */
export const KIND_GROUP_LABEL: Record<WorkspaceItemKind, string> = {
  attachment: "المرفقات",
  note: "الملاحظات",
  agent_search: "نتائج البحث",
  agent_writing: "المسودات",
  convo_context: "ملخص المحادثة",
  references: "المراجع",
};

/** Group order — web WorkspaceList `KIND_ORDER`. */
export const KIND_ORDER: readonly WorkspaceItemKind[] = [
  "agent_writing",
  "agent_search",
  "note",
  "attachment",
  "references",
  "convo_context",
];

/** Card pill — web WorkspaceCard `KIND_LABEL`. */
export const KIND_LABEL: Record<WorkspaceItemKind, string> = {
  attachment: "مرفق",
  note: "ملاحظة",
  agent_search: "بحث",
  agent_writing: "مسودة",
  convo_context: "ملخص المحادثة",
  references: "مراجع",
};

/** `metadata.subtype` wins over the kind — web WorkspaceCard `SUBTYPE_LABEL`. */
export const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

/** Web WorkspaceList empty state. */
export const EMPTY_LIST_TEXT = "لا توجد عناصر بعد";
/** Web WorkspacePane / WorkspaceItemViewer error state. */
export const ITEM_LOAD_ERROR = "حدث خطأ في تحميل العنصر";

export function subtypeOf(item: Pick<WorkspaceItem, "metadata">): string | undefined {
  const raw = (item.metadata as { subtype?: unknown } | undefined)?.subtype;
  return typeof raw === "string" && raw.length > 0 ? raw : undefined;
}

/** The card's pill: subtype label (or the raw subtype), else the kind label. */
export function cardLabel(item: Pick<WorkspaceItem, "kind" | "metadata">): string {
  const subtype = subtypeOf(item);
  if (subtype) return SUBTYPE_LABEL[subtype] ?? subtype;
  return KIND_LABEL[item.kind] ?? item.kind;
}

/** «WI-3» — the alias the agents say in chat; null when the item has none
 *  (case-only items, pre-052 rows, a bad value). */
export function wiAlias(seq: number | null | undefined): string | null {
  if (typeof seq !== "number" || !Number.isSafeInteger(seq) || seq < 1) return null;
  return `WI-${seq}`;
}

/**
 * Hidden BY THE USER («إخفاء»): `is_visible === false`. `convo_context` is
 * written hidden by the memory agent on purpose (it is internal context, never
 * a chip) and the web lists it like any other item — so it never counts as
 * user-hidden and is never filtered out.
 */
export function isUserHidden(item: Pick<WorkspaceItem, "kind" | "is_visible">): boolean {
  return item.is_visible === false && item.kind !== "convo_context";
}

export function hiddenCount(items: readonly WorkspaceItem[]): number {
  return items.filter(isUserHidden).length;
}

/** What the list shows: everything when `showHidden`, else the user-hidden
 *  ones are left out. Server order (newest first) is kept. */
export function visibleItems(items: readonly WorkspaceItem[], showHidden: boolean): WorkspaceItem[] {
  return showHidden ? [...items] : items.filter((i) => !isUserHidden(i));
}

export interface WorkspaceGroup {
  kind: WorkspaceItemKind;
  label: string;
  items: WorkspaceItem[];
}

/** Items grouped by kind in the web's order; empty groups and unknown kinds
 *  are dropped (the web only renders `KIND_ORDER` kinds). */
export function groupItems(items: readonly WorkspaceItem[]): WorkspaceGroup[] {
  const byKind = new Map<WorkspaceItemKind, WorkspaceItem[]>();
  for (const item of items) {
    const list = byKind.get(item.kind);
    if (list) list.push(item);
    else byKind.set(item.kind, [item]);
  }
  return KIND_ORDER.filter((k) => byKind.has(k)).map((kind) => ({
    kind,
    label: KIND_GROUP_LABEL[kind],
    items: byKind.get(kind) ?? [],
  }));
}

/** The chat menu entry: «مساحة العمل (n)», n = what the list shows by default;
 *  no count while unknown. */
export function workspaceMenuLabel(count: number | null): string {
  return count === null ? "مساحة العمل" : `مساحة العمل (${count})`;
}

export function toggleHiddenLabel(showHidden: boolean, count: number): string {
  return showHidden ? "إخفاء العناصر المخفية" : `عرض العناصر المخفية (${count})`;
}

// --- إخفاء / حذف -------------------------------------------------------------
// The web has the endpoints and hooks (useToggleWorkspaceVisibility,
// useDeleteWorkspaceItem) but no button for them yet; the app offers both
// from the action bar's «المزيد» (plan 05 §W3). Nothing on the shared demo
// conversation (one row for every account; the server refuses writes there),
// nothing on `convo_context` (system-owned running summary).

export interface ManageSpec {
  hide: boolean;
  remove: boolean;
}

export function manageSpec(kind: WorkspaceItemKind, isDemo: boolean): ManageSpec {
  if (isDemo || kind === "convo_context") return { hide: false, remove: false };
  return { hide: true, remove: true };
}

export function visibilityActionLabel(isVisible: boolean): string {
  return isVisible ? "إخفاء عن ريحان" : "إظهار لريحان";
}

export const VISIBILITY_HINT = {
  hide: "لن يطّلع عليه ريحان في الردود القادمة، ويبقى هنا",
  show: "يعود ريحان إلى الاطّلاع عليه في الردود القادمة",
} as const;

export const HIDDEN_BADGE = "مخفي";

export const DELETE_CONFIRM = {
  title: "حذف العنصر؟",
  /** An `agent_search` also loses its reference rows server-side. */
  message: "سيُحذف هذا العنصر من مساحة العمل، ولا يمكن التراجع عن ذلك.",
  confirm: "حذف",
} as const;

export const MANAGE_TOASTS = {
  hidden: "أُخفي العنصر عن ريحان",
  shown: "أصبح العنصر ظاهراً لريحان",
  deleted: "تم حذف العنصر",
} as const;

// --- «إضافة عنصر» (web WorkspaceAddMenu) --------------------------------------

export const ADD_ITEM_LABEL = "إضافة عنصر";
export const NEW_NOTE_TITLE = "ملاحظة جديدة";
export const NEW_REFERENCES_TITLE = "مراجع";

// --- note editor (web NoteEditor / MarkdownDocEditor) -------------------------

export const NOTE_TITLE_PLACEHOLDER = "عنوان الملاحظة...";
export const NOTE_BODY_PLACEHOLDER = "اكتب ملاحظاتك هنا...";

export interface NoteBaseline {
  title: string;
  content: string;
}

/**
 * The autosave PATCH for a note — web MarkdownDocEditor `flushPendingSave`:
 * the title is compared and sent TRIMMED; a blank title skips the save
 * altogether; only changed fields go out; `null` = nothing to send.
 */
export function notePatch(base: NoteBaseline, title: string, content: string): { title?: string; content_md?: string } | null {
  const nextTitle = title.trim();
  if (!nextTitle) return null;
  const titleChanged = nextTitle !== base.title;
  const contentChanged = content !== base.content;
  if (!titleChanged && !contentChanged) return null;
  return {
    ...(titleChanged ? { title: nextTitle } : {}),
    ...(contentChanged ? { content_md: content } : {}),
  };
}

// --- attachment (web AttachmentRenderer) ---------------------------------------

export interface AttachmentMeta {
  filename?: string;
  mimeType?: string;
  ocrStatus?: string;
  purged: boolean;
}

export function attachmentMeta(item: Pick<WorkspaceItem, "metadata">): AttachmentMeta {
  const m = (item.metadata ?? {}) as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" && v.length > 0 ? v : undefined);
  return {
    filename: str(m.filename),
    mimeType: str(m.mime_type),
    ocrStatus: str(m.ocr_status),
    purged: Boolean(m.original_purged_at),
  };
}

/** Which screen the attachment viewer shows. */
export type AttachmentView =
  /** The extracted text (primary). `purged` → no «الملف الأصلي» toggle. */
  | { kind: "ocr"; purged: boolean }
  /** Purged and nothing extracted — only the reason is left. */
  | { kind: "purged-empty"; detail: string }
  /** The original file; `canBack` = there is extracted text to go back to,
   *  else `banner` explains why there is none. */
  | { kind: "original"; canBack: boolean; banner: string | null };

export const ATTACHMENT_TEXTS = {
  original: "الملف الأصلي",
  extracted: "النص المستخرج",
  copyExtracted: "نسخ النص المستخرج",
  purged: "حُذف الملف الأصلي بعد انتهاء مدة الاحتفاظ",
  purgedEmpty: "ولم يكن يحتوي على نص قابل للاستخراج",
  purgedNoText: "ولم يُستخرج منه نص",
  loading: "جارٍ تحميل المرفق...",
  loadFailed: "تعذّر تحميل الملف",
  openFile: "فتح الملف",
  cannotPreview: "لا يمكن عرض هذا النوع داخل التطبيق",
} as const;

/** Web `OcrStatusNote`. */
export function ocrStatusText(status: string | undefined): string {
  if (!status) return "لم يُستخرج نص المستند بعد — يُستخرج تلقائيًا عند إرسال الرسالة";
  if (status === "empty") return "لا يحتوي المستند على نص قابل للاستخراج";
  return "تعذّر استخراج نص هذا المستند";
}

export function attachmentView(item: Pick<WorkspaceItem, "content_md" | "metadata">, showOriginal: boolean): AttachmentView {
  const meta = attachmentMeta(item);
  const hasText = (item.content_md ?? "").trim().length > 0;
  if (hasText && (meta.purged || !showOriginal)) return { kind: "ocr", purged: meta.purged };
  if (meta.purged) {
    return {
      kind: "purged-empty",
      detail: meta.ocrStatus === "empty" ? ATTACHMENT_TEXTS.purgedEmpty : ATTACHMENT_TEXTS.purgedNoText,
    };
  }
  return { kind: "original", canBack: hasText, banner: hasText ? null : ocrStatusText(meta.ocrStatus) };
}

/** How the original opens on a phone: images inline (web `<img>`), PDFs in
 *  the in-app browser (web `<iframe>`), anything else the browser too (the
 *  web's download link). */
export function originalFileMode(mimeType: string | undefined): "image" | "browser" {
  return (mimeType ?? "").startsWith("image/") ? "image" : "browser";
}

// --- convo_context / references -----------------------------------------------

export const CONVO_CONTEXT_EMPTY = "لم يُولَّد ملخّص للمحادثة بعد";
export const REFERENCES_STUB_BANNER = "قيد التطوير — قائمة المراجع";
