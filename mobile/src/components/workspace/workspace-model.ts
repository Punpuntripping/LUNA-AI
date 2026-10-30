import { isWiPreviewLocked, wiPreviewVisibleContent, type WorkspaceItem, type WorkspaceItemKind } from "@contracts";

// Pure rules of the workspace screen — a port of the web's chat-pane
// workspace (frontend/components/workspace/WorkspaceList, WorkspaceCard,
// WiBadge, AttachmentRenderer, ConvoContextViewer, ReferencesRenderer,
// WorkspaceAddMenu, NoteEditor). No React Native here so `npm test` covers it.

// --- WI preview lock (.claude/plans/wi_preview_lock.md) ------------------------

/** The part of an item the viewers read the lock from. */
type PreviewFields = Pick<WorkspaceItem, "content_md" | "preview">;

/**
 * What a viewer may do with an item's body. The server sends `preview` only on
 * a preview-locked item (a free user's agent report / draft created after
 * launch): the body is then its visible part, read-only, and it never leaves
 * the app — no edit / autosave, مشاركة, حفظ كمدونة or PDF / Word; نسخ stays
 * and copies the visible part only. Without `preview` (every other item, and
 * always while the server flag is off) everything is as before.
 */
export interface PreviewAccess {
  /** Render `wiPreviewVisibleContent(item)` + WiPreviewWall. */
  locked: boolean;
  /** Body edits and their autosave (a title-only edit is the server's call). */
  editBody: boolean;
  /** مشاركة / حفظ كمدونة. */
  publish: boolean;
  /** PDF / Word. */
  export: boolean;
}

export function previewAccess(item: PreviewFields): PreviewAccess {
  const locked = isWiPreviewLocked(item);
  return { locked, editBody: !locked, publish: !locked, export: !locked };
}

/**
 * How many sources only the hidden part cites («وN مصادر أخرى في الجزء
 * المقفل»): the `/references` answer's `hidden_count` when it has one (it goes
 * with the list on screen), else the item's own `preview` block; 0 on an open
 * item.
 */
export function lockedRefCount(item: PreviewFields, refsHiddenCount: number | undefined): number {
  if (!isWiPreviewLocked(item)) return 0;
  const n = refsHiddenCount ?? item.preview?.hidden_ref_count ?? 0;
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

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

/** `metadata.subtype` wins over the kind — web WorkspaceCard `SUBTYPE_LABEL`,
 *  plus the writer subtypes the web map lacks (agents/writer/models.py
 *  `WriterSubtype`: defense_brief, letter, statement_of_claim), same
 *  no-tashkeel style. */
export const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
  defense_brief: "مذكرة دفاع",
  letter: "خطاب",
  statement_of_claim: "صحيفة دعوى",
};

/** Web WorkspaceList empty state. */
export const EMPTY_LIST_TEXT = "لا توجد عناصر بعد";
/** Web WorkspacePane / WorkspaceItemViewer error state. */
export const ITEM_LOAD_ERROR = "حدث خطأ في تحميل العنصر";

export function subtypeOf(item: Pick<WorkspaceItem, "metadata">): string | undefined {
  const raw = (item.metadata as { subtype?: unknown } | undefined)?.subtype;
  return typeof raw === "string" && raw.length > 0 ? raw : undefined;
}

/** Neutral Arabic pill for a kind the app does not know yet. */
const UNKNOWN_KIND_LABEL = "عنصر";

/** The card's pill: the subtype's label, else the kind label. An unknown
 *  subtype (or kind) never shows its raw English key. */
export function cardLabel(item: Pick<WorkspaceItem, "kind" | "metadata">): string {
  const subtype = subtypeOf(item);
  const bySubtype = subtype ? SUBTYPE_LABEL[subtype] : undefined;
  return bySubtype ?? KIND_LABEL[item.kind] ?? UNKNOWN_KIND_LABEL;
}

// «افتح ال…» under an answer — exactly web MessageBubble DEFINITE_SUBTYPE /
// DEFINITE_KIND (other writer subtypes fall back to «المسودة», as on the web).
// Lookups, not morphology: «نتيجة البحث» / «ملخص المحادثة» are definite by إضافة.
const DEFINITE_SUBTYPE: Record<string, string> = {
  report: "التقرير",
  contract: "العقد",
  memo: "المذكرة",
  summary: "الملخص",
  memory_file: "الذاكرة",
  legal_opinion: "الرأي القانوني",
  legal_synthesis: "التحليل القانوني",
};
const DEFINITE_KIND: Record<WorkspaceItemKind, string> = {
  attachment: "المرفق",
  note: "الملاحظة",
  agent_search: "نتيجة البحث",
  agent_writing: "المسودة",
  convo_context: "ملخص المحادثة",
  references: "المراجع",
};

/** The output's name with the article; «المصدر» while unknown — never a raw key. */
export function definiteArtifactName(entry: { kind: string; subtype?: string } | undefined): string {
  if (!entry) return "المصدر";
  const bySubtype = entry.subtype ? DEFINITE_SUBTYPE[entry.subtype] : undefined;
  return bySubtype ?? DEFINITE_KIND[entry.kind as WorkspaceItemKind] ?? "المصدر";
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
// OFF: the web has no button for these, and the owner did not approve adding
// them to the app (2026-10-20). Keep both false until the owner asks for them.

export interface ManageSpec {
  hide: boolean;
  remove: boolean;
}

export function manageSpec(_kind: WorkspaceItemKind, _isDemo: boolean): ManageSpec {
  return { hide: false, remove: false };
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
 * `bodyLocked` (a preview-locked item): the body is never sent — the server
 * would refuse it (402) and it holds only the visible part.
 */
export function notePatch(
  base: NoteBaseline,
  title: string,
  content: string,
  bodyLocked = false,
): { title?: string; content_md?: string } | null {
  const nextTitle = title.trim();
  if (!nextTitle) return null;
  const titleChanged = nextTitle !== base.title;
  const contentChanged = !bodyLocked && content !== base.content;
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

export function attachmentView(item: Pick<WorkspaceItem, "content_md" | "metadata" | "preview">, showOriginal: boolean): AttachmentView {
  const meta = attachmentMeta(item);
  const hasText = wiPreviewVisibleContent(item).trim().length > 0;
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
