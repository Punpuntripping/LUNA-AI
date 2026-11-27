// ==========================================
// «الملفات» — the user's file library (.claude/plans/my_files.md §11; the plan
// calls it «ملفاتي», the owner renamed the section «الملفات» on 2026-11-19)
// ==========================================
// Wire types for `/api/v1/files*` (Phase 1 + 1b) and the pure helpers both
// apps use to arrange, search and label a library. Field names are the §11
// contract the backend is built against — never rename them here.
//
// Pure TypeScript only (README rules): no React, no platform globals, relative
// imports only.

import type { WorkspaceItem } from "./agents";
import { arResetDate, toLatinDigits } from "./numerals";

// ------------------------------------------
// Wire types — §11 verbatim
// ------------------------------------------

/** agents/memory/artifact_summarizer/models.py `DocType`. */
export type FileDocType =
  | "judgment"
  | "hearing_minutes"
  | "claim"
  | "memo"
  | "contract"
  | "chat"
  | "letter"
  | "financial"
  | "official_record"
  | "expert_report"
  | "statement"
  | "user_notes"
  | "reference"
  | "other";

export interface LibraryFile {
  item_id: string;
  title: string;
  /** metadata.filename (the upload's own name). */
  filename: string | null;
  mime_type: string | null;
  /** null = no type yet. */
  doc_type: FileDocType | null;
  /** Plain text, at most 280 chars, "" when there is no summary. */
  summary_preview: string;
  page_count: number | null;
  /** Length of the extracted text. */
  chars: number;
  /** A map exists and is not stale. */
  has_sections: boolean;
  /** The original file can still be opened. */
  original_available: boolean;
  conversation_id: string | null;
  wi_seq: number | null;
  /** Identical files folded into this row (1 = no copies). */
  copies: number;
  /** ISO 8601. */
  created_at: string;
  /**
   * «ننصح بإعادة إرفاق هذا الملف»: the stored extraction is known to be shorter
   * than the file. The UI advises and gives no reason. Absent on an older backend.
   */
  reattach_advised?: boolean;
  /** The retention sweep removed the original (the only case the viewer says so). */
  original_purged?: boolean;
  /** Phase 2: the `user_files` row behind this item; present only while that read switch is on. */
  file_id?: string;
}

/** `GET /files`. */
export interface LibraryFileListResponse {
  /** Newest first, at most 500. */
  files: LibraryFile[];
  total: number;
  /** Keys: a FileDocType, or "untyped". */
  type_counts: Record<string, number>;
}

export interface FileSection {
  /** Document order. */
  seq: number;
  /** Arabic description of the section. */
  what: string;
  /** value === 'metadata'. */
  is_metadata: boolean;
  page_from: number | null;
  page_to: number | null;
  /** Index into `documents`; null when the file is one document. */
  document_index: number | null;
  /** The section's original text. */
  content_md: string;
  /**
   * seg-v5.2 — whose words, findings or decision the section carries. A fixed key
   * (claimant, respondent, court, expert…). null when the document does not say, and on a
   * map made before seg-v5.2.
   */
  owner_class?: string | null;
  /** That owner in the document's own role words («المدعى عليه الثاني», «الخبير»). */
  owner?: string | null;
  /** The party that filed or relies on the section, when it is not the owner. */
  brought_by?: string | null;
  /**
   * «النصوص المثبّتة» — the official articles this section cites, in the
   * library's current wording. Empty or absent unless `FILE_PINNED_TEXTS_UI_*`
   * is on for the user (and on an older backend).
   */
  pinned_texts?: FilePinnedText[];
}

/** One official article a file cites (`FILE_PINNED_TEXTS_UI_*`). */
export interface FilePinnedText {
  article_id: string;
  /** «المادة 77 من نظام العمل». */
  title: string;
  /** The official wording. */
  text: string;
}

/** `GET /files/{item_id}`. */
export interface LibraryFileDetail extends LibraryFile {
  /** Full markdown summary. */
  summary: string | null;
  /** Full extracted text. */
  content_md: string;
  /** Empty unless the file holds 2 or more documents. */
  documents: { name: string }[];
  /** null = no map or a stale map. */
  sections: FileSection[] | null;
  /**
   * A file shown as full text (`sections: null`): the articles it cites, each
   * once. With sections they ride on each section instead.
   */
  pinned_texts?: FilePinnedText[];
}

// DELETE /files/{item_id} → 204

/** `POST /files/match` (Phase 1b). */
export interface FileMatchRequest {
  filename: string;
  size_bytes: number;
  mime_type: string;
  conversation_id?: string;
}

export interface FileMatch {
  item_id: string;
  title: string;
  created_at: string;
  same_conversation: boolean;
  wi_seq: number | null;
}

export interface FileMatchResponse {
  match: FileMatch | null;
}

// ------------------------------------------
// Type labels (plan §3.4)
// ------------------------------------------

/**
 * Every type, in the order the plan lists the group headings. Used to break
 * ties between groups of the same size so the order never depends on the
 * response order.
 */
export const FILE_DOC_TYPES: readonly FileDocType[] = [
  "judgment",
  "hearing_minutes",
  "claim",
  "memo",
  "contract",
  "chat",
  "letter",
  "financial",
  "official_record",
  "expert_report",
  "statement",
  "user_notes",
  "reference",
  "other",
];

/**
 * The pill on a card (singular). `chat` is «مراسلة», not «محادثة», for the
 * same reason the plan renames its heading: it must not read like the
 * «المحادثات» tab beside it. `other` has a label for completeness but
 * `fileTypePillLabel` shows no pill for it — «أخرى» on a card says nothing.
 */
export const FILE_TYPE_LABELS: Record<FileDocType, string> = {
  judgment: "حكم",
  hearing_minutes: "محضر جلسة",
  claim: "صحيفة دعوى",
  memo: "مذكرة",
  contract: "عقد",
  chat: "مراسلة",
  letter: "خطاب",
  financial: "مستند مالي",
  official_record: "سجل رسمي",
  expert_report: "تقرير خبرة",
  statement: "إقرار",
  user_notes: "ملاحظاتي",
  reference: "مرجع",
  other: "أخرى",
};

/** Group heading / filter chip (plural) — plan §3.4, copied verbatim. */
export const FILE_TYPE_GROUP_LABELS: Record<FileDocType, string> = {
  judgment: "أحكام",
  hearing_minutes: "محاضر جلسات",
  claim: "صحف دعوى ولوائح",
  memo: "مذكرات",
  contract: "عقود",
  chat: "مراسلات ومحادثات",
  letter: "خطابات وإنذارات",
  financial: "مستندات مالية",
  official_record: "سجلات رسمية",
  expert_report: "تقارير خبرة",
  statement: "إقرارات وشهادات",
  user_notes: "ملاحظاتي وجداولي",
  reference: "مراجع ونصوص",
  other: "أخرى",
};

const DOC_TYPE_SET: ReadonlySet<string> = new Set(FILE_DOC_TYPES);

/**
 * A wire value → a known type, or null. A value from a newer backend that this
 * build does not know is treated as "no type" (it lands under «أخرى» with no
 * pill) rather than rendering an English key.
 */
export function coerceFileDocType(value: unknown): FileDocType | null {
  return typeof value === "string" && DOC_TYPE_SET.has(value)
    ? (value as FileDocType)
    : null;
}

/** The group a file belongs to: untyped files go under «أخرى». */
export function fileGroupType(docType: unknown): FileDocType {
  return coerceFileDocType(docType) ?? "other";
}

/**
 * Five families for tinting a file tile — the app deliberately avoids a colour
 * per type. Court papers · contracts and official records · money ·
 * correspondence · everything else (incl. untyped).
 */
export type FileTypeFamily = "court" | "records" | "money" | "correspondence" | "general";

export const FILE_TYPE_FAMILY: Record<FileDocType, FileTypeFamily> = {
  judgment: "court",
  hearing_minutes: "court",
  claim: "court",
  memo: "court",
  expert_report: "court",
  contract: "records",
  official_record: "records",
  statement: "records",
  financial: "money",
  chat: "correspondence",
  letter: "correspondence",
  user_notes: "general",
  reference: "general",
  other: "general",
};

export function fileTypeFamily(docType: unknown): FileTypeFamily {
  return FILE_TYPE_FAMILY[fileGroupType(docType)];
}

/** The pill text, or null when the card shows no pill (untyped or `other`). */
export function fileTypePillLabel(docType: unknown): string | null {
  const type = coerceFileDocType(docType);
  if (!type || type === "other") return null;
  return FILE_TYPE_LABELS[type];
}

// ------------------------------------------
// Arranging a library (plan §3.4)
// ------------------------------------------

type Datable = { created_at: string };
type Typed = { doc_type: FileDocType | string | null };

/**
 * Headings appear only with MORE than this many files, in 2 or more types.
 * The median user has 3 files; headings over 3 cards are noise.
 */
export const FILE_GROUPING_MIN_FILES = 6;

function timeOf(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? 0 : ms;
}

/** Newest first; equal (or unparsable) dates keep their incoming order. */
export function sortFilesNewestFirst<T extends Datable>(files: readonly T[]): T[] {
  return files
    .map((file, index) => ({ file, index, t: timeOf(file.created_at) }))
    .sort((a, b) => b.t - a.t || a.index - b.index)
    .map((entry) => entry.file);
}

export interface FileGroup<T> {
  type: FileDocType;
  label: string;
  files: T[];
}

export type FileListLayout<T> =
  | { kind: "flat"; files: T[] }
  | { kind: "grouped"; groups: FileGroup<T>[] };

/**
 * Group order: largest first; ties by the plan's type order. «أخرى» always
 * closes the list whatever its size — it holds every untyped file, and before
 * the type backfill runs it is the largest group by far, so "largest first"
 * alone would bury every typed group under it.
 */
function compareGroups<T>(a: FileGroup<T>, b: FileGroup<T>): number {
  if (a.type === "other" && b.type !== "other") return 1;
  if (b.type === "other" && a.type !== "other") return -1;
  if (b.files.length !== a.files.length) return b.files.length - a.files.length;
  return FILE_DOC_TYPES.indexOf(a.type) - FILE_DOC_TYPES.indexOf(b.type);
}

function bucketByType<T extends Typed>(files: readonly T[]): Map<FileDocType, T[]> {
  const buckets = new Map<FileDocType, T[]>();
  for (const file of files) {
    const type = fileGroupType(file.doc_type);
    const bucket = buckets.get(type);
    if (bucket) bucket.push(file);
    else buckets.set(type, [file]);
  }
  return buckets;
}

/**
 * The §3.4 body rule, applied to whatever set is on screen (after search and
 * the type chip): with more than 6 files in 2 or more types, groups under type
 * headings (largest first, «أخرى» last, newest first inside); otherwise one
 * flat list, newest first.
 */
export function arrangeLibraryFiles<T extends Typed & Datable>(
  files: readonly T[],
): FileListLayout<T> {
  const sorted = sortFilesNewestFirst(files);
  const buckets = bucketByType(sorted);
  if (sorted.length <= FILE_GROUPING_MIN_FILES || buckets.size < 2) {
    return { kind: "flat", files: sorted };
  }
  const groups: FileGroup<T>[] = Array.from(buckets, ([type, groupFiles]) => ({
    type,
    label: FILE_TYPE_GROUP_LABELS[type],
    files: groupFiles,
  }));
  groups.sort(compareGroups);
  return { kind: "grouped", groups };
}

export interface FileTypeChip {
  type: FileDocType;
  label: string;
  count: number;
}

/**
 * One chip per type the user actually has (untyped counted into «أخرى»), in
 * the same order as the groups. «الكل» is the caller's own first chip.
 */
export function fileTypeChips<T extends Typed>(files: readonly T[]): FileTypeChip[] {
  const groups: FileGroup<T>[] = Array.from(bucketByType(files), ([type, groupFiles]) => ({
    type,
    label: FILE_TYPE_GROUP_LABELS[type],
    files: groupFiles,
  }));
  groups.sort(compareGroups);
  return groups.map((g) => ({ type: g.type, label: g.label, count: g.files.length }));
}

export type FileTypeFilter = FileDocType | "all";

export function filterFilesByType<T extends Typed>(
  files: readonly T[],
  filter: FileTypeFilter,
): T[] {
  if (filter === "all") return files.slice();
  return files.filter((file) => fileGroupType(file.doc_type) === filter);
}

// ------------------------------------------
// Client search (title + summary_preview + filename)
// ------------------------------------------

/** Harakat, Quranic marks, superscript alef and tatweel. */
const ARABIC_MARKS_RE = /[ؐ-ًؚ-ٰٟۖ-ۭـ]/g;

/**
 * Fold the spellings a reader does not distinguish while searching: case,
 * harakat, tatweel, hamza seats on alef (أ إ آ ٱ → ا), ى → ي, ة → ه, ؤ → و,
 * ئ → ي, Arabic-Indic digits → Latin, and runs of whitespace.
 */
export function normalizeArabicSearchText(text: string): string {
  return toLatinDigits(text)
    .toLowerCase()
    .replace(ARABIC_MARKS_RE, "")
    .replace(/[أإآٱ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/ؤ/g, "و")
    .replace(/ئ/g, "ي")
    .replace(/\s+/g, " ")
    .trim();
}

type Searchable = {
  title: string;
  summary_preview?: string | null;
  filename?: string | null;
};

/**
 * Every word of the query must appear somewhere in the title, the summary
 * preview or the file name. An empty query returns the list unchanged.
 */
export function searchLibraryFiles<T extends Searchable>(
  files: readonly T[],
  query: string,
): T[] {
  const tokens = normalizeArabicSearchText(query).split(" ").filter(Boolean);
  if (tokens.length === 0) return files.slice();
  return files.filter((file) => {
    const haystack = normalizeArabicSearchText(
      `${file.title} ${file.summary_preview ?? ""} ${file.filename ?? ""}`,
    );
    return tokens.every((token) => haystack.includes(token));
  });
}

// ------------------------------------------
// Labels — Latin digits only
// ------------------------------------------

/**
 * Arabic counted-noun agreement (CLDR `ar` plural categories): 1 · 2 ·
 * n%100 in 3–10 (plural) · n%100 in 11–99 (singular accusative) · the rest
 * (100, 101, 102, …: singular genitive). Latin digits.
 */
function arCount(
  n: number,
  forms: { one: string; two: string; few: string; many: string; other: string },
): string {
  if (n === 1) return forms.one;
  if (n === 2) return forms.two;
  const mod = n % 100;
  if (mod >= 3 && mod <= 10) return `${n} ${forms.few}`;
  if (mod >= 11 && mod <= 99) return `${n} ${forms.many}`;
  return `${n} ${forms.other}`;
}

/** «صفحة واحدة» · «صفحتان» · «3 صفحات» · «11 صفحة» · «103 صفحات»; "" when unknown. */
export function formatPageCount(pages: number | null | undefined): string {
  if (typeof pages !== "number" || !Number.isFinite(pages) || pages < 1) return "";
  return arCount(Math.round(pages), {
    one: "صفحة واحدة",
    two: "صفحتان",
    few: "صفحات",
    many: "صفحة",
    other: "صفحة",
  });
}

/** «ملف واحد» · «ملفان» · «3 ملفات» · «120 ملفًا»… ; "" for 0 or less. */
export function formatFileCount(count: number): string {
  if (!Number.isFinite(count) || count < 1) return "";
  return arCount(Math.round(count), {
    one: "ملف واحد",
    two: "ملفان",
    few: "ملفات",
    many: "ملفًا",
    other: "ملف",
  });
}

/** «خيط واحد» · «خيطان» · «3 خيوط» · «11 خيطًا»… — an angle is a «خيط» to the user
 * (angle_map_ui.md); "" for 0 or less. */
export function formatAngleCount(count: number): string {
  if (!Number.isFinite(count) || count < 1) return "";
  return arCount(Math.round(count), {
    one: "خيط واحد",
    two: "خيطان",
    few: "خيوط",
    many: "خيطًا",
    other: "خيط",
  });
}

/** «ص 3–5», «ص 4»; "" when the section has no page numbers. */
export function formatSectionPages(
  from: number | null | undefined,
  to: number | null | undefined,
): string {
  const a = typeof from === "number" && Number.isFinite(from) ? from : null;
  const b = typeof to === "number" && Number.isFinite(to) ? to : null;
  if (a === null && b === null) return "";
  const lo = Math.min(a ?? b!, b ?? a!);
  const hi = Math.max(a ?? b!, b ?? a!);
  return lo === hi ? `ص ${lo}` : `ص ${lo}–${hi}`;
}

const DAY_MS = 86_400_000;

function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * A file's date: «اليوم», «أمس», otherwise the full date «31 أكتوبر 2026»
 * (Gregorian, Latin digits). "" when the value cannot be read.
 */
export function formatFileDate(iso: string, now: Date = new Date()): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const days = Math.round((startOfLocalDay(now) - startOfLocalDay(new Date(ms))) / DAY_MS);
  if (days === 0) return "اليوم";
  if (days === 1) return "أمس";
  return arResetDate(iso);
}

// ------------------------------------------
// Card model — one shape for a library row and a pane attachment
// ------------------------------------------

/**
 * §3.2 states. A library row is always `ready` (it has extracted text).
 * `pending` = not read yet: extraction runs when the message carrying the file
 * is sent, so a file attached in the pane waits for that send. `processing` is
 * only the real in-progress read (the conversation's send is extracting now).
 */
export type FileProcessingStatus = "ready" | "pending" | "processing" | "empty" | "failed";

export const FILE_STATUS_COPY: Record<Exclude<FileProcessingStatus, "ready">, string> = {
  // AttachmentRenderer's «لم يُستخرج نص المستند بعد — يُستخرج تلقائيًا عند
  // إرسال الرسالة», shortened to fit a card.
  pending: "لم يُستخرج النص بعد — يُستخرج عند إرسال الرسالة",
  processing: "جارٍ استخراج النص…",
  empty: "لا يحتوي على نص قابل للاستخراج",
  failed: "تعذّر استخراج النص",
};

/**
 * From `metadata.ocr_status` (agents/memory/ocr_extractor): absent = not read
 * yet (it is read when the message is sent — `extracting` says that read is
 * running right now), `done` = text in `content_md`, `empty` = nothing to
 * extract, `failed` / `skipped_*` = extraction failed.
 */
export function fileStatusFromOcr(
  ocrStatus: unknown,
  hasText: boolean,
  extracting = false,
): FileProcessingStatus {
  if (hasText) return "ready";
  if (ocrStatus === undefined || ocrStatus === null || ocrStatus === "") {
    return extracting ? "processing" : "pending";
  }
  if (ocrStatus === "empty" || ocrStatus === "done") return "empty";
  return "failed";
}

export interface FileCardModel {
  item_id: string;
  title: string;
  doc_type: FileDocType | null;
  /** "" = no summary. */
  summary_preview: string;
  page_count: number | null;
  created_at: string;
  wi_seq: number | null;
  status: FileProcessingStatus;
  /** «ننصح بإعادة إرفاق هذا الملف». */
  reattach_advised: boolean;
}

export function libraryFileCardModel(file: LibraryFile): FileCardModel {
  return {
    item_id: file.item_id,
    title: file.title,
    doc_type: coerceFileDocType(file.doc_type),
    summary_preview: file.summary_preview ?? "",
    page_count: file.page_count ?? null,
    created_at: file.created_at,
    wi_seq: file.wi_seq ?? null,
    status: "ready",
    reattach_advised: file.reattach_advised === true,
  };
}

function firstPositiveInt(...values: unknown[]): number | null {
  for (const v of values) {
    if (typeof v === "number" && Number.isFinite(v) && v >= 1) return Math.round(v);
  }
  return null;
}

/**
 * A pane `attachment` item → the same card model (reads `metadata`).
 * `extracting`: the conversation's send is reading its unread files right now.
 */
export function workspaceAttachmentCardModel(
  item: WorkspaceItem,
  options: { extracting?: boolean } = {},
): FileCardModel {
  const meta = (item.metadata ?? {}) as Record<string, unknown>;
  const hasText = (item.content_md ?? "").trim().length > 0;
  return {
    item_id: item.item_id,
    title: item.title,
    doc_type: coerceFileDocType(meta.doc_type),
    summary_preview: item.summary_preview ?? "",
    page_count: firstPositiveInt(meta.ocr_page_total, meta.page_count, meta.ocr_pages),
    created_at: item.created_at,
    wi_seq: item.wi_seq ?? null,
    status: fileStatusFromOcr(meta.ocr_status, hasText, options.extracting ?? false),
    reattach_advised: item.reattach_advised === true,
  };
}

// ------------------------------------------
// Section owners (seg-v5.2: owner_class / owner / brought_by)
// ------------------------------------------

/**
 * Arabic name of an owner class: the filter chip's label, and what a row shows
 * when the map gave the class without the document's own words. Keys are the
 * segmenter's `OWNER_CLASSES` minus `unknown` (never shown). First drafts for
 * the owner to approve.
 */
export const SECTION_OWNER_CLASS_LABELS: Record<string, string> = {
  claimant: "المدعي",
  respondent: "المدعى عليه",
  prosecution: "النيابة العامة",
  victim: "المجني عليه",
  intervenor: "المتدخل",
  both_parties: "الطرفان",
  court: "المحكمة",
  court_officer: "موظف المحكمة",
  arbitrator: "هيئة التحكيم",
  committee: "اللجنة",
  conciliator: "المصلح",
  investigator: "جهة التحقيق",
  inspector: "جهة التفتيش",
  expert: "الخبير",
  translator: "المترجم",
  witness: "الشاهد",
  authority: "جهة رسمية",
  third_party: "طرف خارجي",
  contract_party: "طرف في العقد",
  mixed: "أكثر من طرف",
};

/** The class name, or null for a key the app does not know (own keys only — never an inherited one). */
function ownerClassLabel(ownerClass: string | null | undefined): string | null {
  const key = ownerClass ?? "";
  return Object.prototype.hasOwnProperty.call(SECTION_OWNER_CLASS_LABELS, key)
    ? SECTION_OWNER_CLASS_LABELS[key]
    : null;
}

/** How an owner tag is tinted: the two sides of the case, whoever decides or issues, everyone else. */
export type SectionOwnerSide = "claimant" | "respondent" | "official" | "other";

const SECTION_OWNER_SIDES: Record<string, SectionOwnerSide> = {
  claimant: "claimant",
  prosecution: "claimant",
  victim: "claimant",
  respondent: "respondent",
  court: "official",
  court_officer: "official",
  arbitrator: "official",
  committee: "official",
  conciliator: "official",
  investigator: "official",
  inspector: "official",
  authority: "official",
};

export function sectionOwnerSide(ownerClass: string | null | undefined): SectionOwnerSide {
  const key = ownerClass ?? "";
  return Object.prototype.hasOwnProperty.call(SECTION_OWNER_SIDES, key) ? SECTION_OWNER_SIDES[key] : "other";
}

type OwnedSection = Pick<FileSection, "owner_class" | "owner" | "brought_by" | "is_metadata">;

/**
 * What a section row shows as its owner: the document's own words, else the
 * class name; null = show nothing (not said, an older map, or a «بيانات المستند»
 * section, which already carries its own tag).
 */
export function sectionOwnerLabel(section: OwnedSection): string | null {
  if (section.is_metadata) return null;
  const own = (section.owner ?? "").trim();
  if (own) return own;
  return ownerClassLabel(section.owner_class);
}

/** «قدّمه: …» — the party that filed the section, when the map names one. */
export function sectionBroughtBy(section: OwnedSection): string | null {
  if (section.is_metadata) return null;
  return (section.brought_by ?? "").trim() || null;
}

export interface SectionOwnerGroup {
  /** The owner class. */
  key: string;
  label: string;
  count: number;
}

/**
 * The owner filter of an opened file: one entry per owner class present among
 * the shown (non-metadata) sections, in order of first appearance. Empty unless
 * the file has at least two — a filter with one choice is noise.
 */
export function sectionOwnerGroups(sections: readonly OwnedSection[]): SectionOwnerGroup[] {
  const order: string[] = [];
  const counts = new Map<string, number>();
  for (const section of sections) {
    if (section.is_metadata) continue;
    const key = section.owner_class ?? "";
    if (ownerClassLabel(key) === null) continue;
    if (!counts.has(key)) order.push(key);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  if (order.length < 2) return [];
  return order.map((key) => ({
    key,
    label: SECTION_OWNER_CLASS_LABELS[key],
    count: counts.get(key) ?? 0,
  }));
}

/** The sections an owner filter leaves on screen (null = all of them). */
export function filterSectionsByOwner<T extends OwnedSection>(
  sections: readonly T[],
  ownerClass: string | null,
): T[] {
  if (!ownerClass) return [...sections];
  return sections.filter((s) => !s.is_metadata && s.owner_class === ownerClass);
}

// ------------------------------------------
// Sections (plan §3.3)
// ------------------------------------------

export interface SectionRun<T> {
  /** null for a one-document file, or a section the map did not place. */
  documentIndex: number | null;
  /** The heading over this run; null = no heading. */
  heading: string | null;
  sections: T[];
}

/**
 * Splits the section list into one run per document, in document order, so a
 * file holding several documents gets one heading per document. With fewer
 * than 2 documents everything is one heading-less run.
 */
export function groupSectionsByDocument<T extends Pick<FileSection, "document_index">>(
  sections: readonly T[],
  documents: readonly { name: string }[],
): SectionRun<T>[] {
  if (documents.length < 2) {
    return [{ documentIndex: null, heading: null, sections: sections.slice() }];
  }
  const runs: SectionRun<T>[] = [];
  for (const section of sections) {
    const index =
      typeof section.document_index === "number" && section.document_index >= 0
        ? section.document_index
        : null;
    const last = runs[runs.length - 1];
    if (last && last.documentIndex === index) {
      last.sections.push(section);
      continue;
    }
    const name = index === null ? "" : (documents[index]?.name ?? "").trim();
    runs.push({
      documentIndex: index,
      heading: index === null ? null : name || `المستند ${index + 1}`,
      sections: [section],
    });
  }
  return runs;
}

// ------------------------------------------
// Same-file approval (plan §3.8, Phase 1b)
// ------------------------------------------

export type FileMatchDecision = "use_existing" | "upload_new";

export interface FileMatchOption {
  value: FileMatchDecision;
  label: string;
  description?: string;
}

/**
 * The two answers, in this order. No «أخرى» and no skip: closing the popup
 * is the same as «رفع نسخة جديدة» — nothing changes without approval.
 */
export const FILE_MATCH_OPTIONS: readonly FileMatchOption[] = [
  {
    value: "use_existing",
    label: "استخدام الموجود",
    description: "يُستخدم النص المستخرج سابقًا، دون استخراج جديد",
  },
  { value: "upload_new", label: "رفع نسخة جديدة" },
];

export interface FileMatchQuestionParts {
  lead: string;
  title: string;
  /** Only for a match inside the same conversation. */
  wiSeq: number | null;
  /** "" when the date cannot be read. */
  date: string;
  ask: string;
}

/** «وجدنا هذا الملف ضمن ملفاتك: «العنوان» · التاريخ — هل نستخدمه؟», as parts. */
export function fileMatchQuestionParts(
  match: Pick<FileMatch, "title" | "created_at" | "same_conversation" | "wi_seq">,
): FileMatchQuestionParts {
  return {
    lead: "وجدنا هذا الملف ضمن ملفاتك:",
    title: match.title,
    wiSeq: match.same_conversation && typeof match.wi_seq === "number" ? match.wi_seq : null,
    date: arResetDate(match.created_at),
    ask: "هل نستخدمه؟",
  };
}

/** The same question as one string (screen readers, the mobile popup). */
export function fileMatchQuestionText(
  match: Pick<FileMatch, "title" | "created_at" | "same_conversation" | "wi_seq">,
): string {
  const p = fileMatchQuestionParts(match);
  const wi = p.wiSeq !== null ? ` (WI-${p.wiSeq})` : "";
  const date = p.date ? ` · ${p.date}` : "";
  return `${p.lead} «${p.title}»${wi}${date} — ${p.ask}`;
}

// ------------------------------------------
// Per-user switch — GET /files/status
// ------------------------------------------

/**
 * `GET /files/status` — is «الملفات» on for the caller? Always 200 for a
 * signed-in user (never 404), `private, no-store`, reads no file data. A build
 * can carry the UI while the backend's `MY_FILES_USERS` limits the feature to
 * some users; the web shows nothing of it unless this says `true`.
 */
export interface FilesStatusResponse {
  enabled: boolean;
  /**
   * «الأوجه» in an opened file (.claude/plans/angle_map_ui.md) — on for this
   * user (`ANGLE_MAP_UI_*`). Only ever true with `enabled`. Absent on an older
   * backend: off.
   */
  angle_maps?: boolean;
  /**
   * «النصوص المثبّتة» on the file, and the conversation's «النصوص المثبّتة»
   * card listed under «الملفات» instead of «العناصر»
   * (`FILE_PINNED_TEXTS_UI_*`). Only ever true with `enabled`. Absent on an
   * older backend: off.
   */
  pinned_texts?: boolean;
}

// ------------------------------------------
// Same-file approval in the attach flow (plan §3.8, D12)
// ------------------------------------------

/**
 * The `POST /files/match` body for a picked file — exactly the three values
 * the upload's `/init` sends, plus the conversation. `null` when there is
 * nothing to ask: no conversation yet (a brand-new chat holds no files), or a
 * file without a name or type.
 */
export function fileMatchRequestFor(
  file: { name: string; size: number; type: string },
  conversationId: string | null | undefined,
): FileMatchRequest | null {
  if (!conversationId || !file.name || !file.type) return null;
  return {
    filename: file.name,
    size_bytes: file.size,
    mime_type: file.type,
    conversation_id: conversationId,
  };
}

/** Where the NEW upload stands when the answer is applied. */
export type MatchedUploadStatus = "queued" | "uploading" | "completed" | "failed" | "cancelled";

/**
 * What to do with the new upload once the existing file is used instead:
 * - `cancel_upload` — still uploading and its session id is known: cancel it.
 * - `cancel_upload_once_started` — still starting (no session id yet): cancel
 *   it the moment `/init` returns (cancelling earlier would leave the session
 *   row that `/init` is about to create).
 * - `delete_new_item` — it already finished: delete the new item.
 * - `none` — it failed / was cancelled: nothing left behind.
 */
export type MatchedUploadCleanup =
  | "none"
  | "cancel_upload"
  | "cancel_upload_once_started"
  | "delete_new_item";

export interface MatchedUploadPlan {
  /** Point the chip (or the pane) at the existing item. */
  useExisting: boolean;
  cleanup: MatchedUploadCleanup;
}

const KEEP_NEW: MatchedUploadPlan = { useExisting: false, cleanup: "none" };

/**
 * The decision for ONE picked file, given the match, the user's answer and the
 * new upload's state. Nothing changes without approval: no match, no answer
 * (popup closed, message sent first) or «رفع نسخة جديدة» → the new upload
 * stays exactly as today. Phase 1b attaches only a copy in the SAME
 * conversation (`same_conversation`); a match that IS the new upload is
 * ignored.
 */
export function planMatchedUpload(input: {
  match: Pick<FileMatch, "item_id" | "same_conversation"> | null;
  answer: FileMatchDecision | null;
  upload: { status: MatchedUploadStatus; itemId: string | null };
}): MatchedUploadPlan {
  const { match, answer, upload } = input;
  if (!match || !match.same_conversation) return KEEP_NEW;
  if (answer !== "use_existing") return KEEP_NEW;
  if (upload.itemId && upload.itemId === match.item_id) return KEEP_NEW;
  switch (upload.status) {
    case "completed":
      return { useExisting: true, cleanup: upload.itemId ? "delete_new_item" : "none" };
    case "queued":
    case "uploading":
      return {
        useExisting: true,
        cleanup: upload.itemId ? "cancel_upload" : "cancel_upload_once_started",
      };
    case "failed":
    case "cancelled":
      return { useExisting: true, cleanup: "none" };
  }
}

/**
 * A late event from an upload that was ALREADY replaced by the existing file:
 * `/init` returning hands over the session id to cancel; a finalize that won
 * the race against the cancel leaves an item to delete; anything else left
 * nothing behind.
 */
export function replacedUploadCleanup(
  event: "started" | "completed" | "failed" | "cancelled",
): Exclude<MatchedUploadCleanup, "cancel_upload_once_started"> {
  if (event === "started") return "cancel_upload";
  if (event === "completed") return "delete_new_item";
  return "none";
}
