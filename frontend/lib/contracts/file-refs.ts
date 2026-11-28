// «من مستنداتك» — the deep-search report citing the user's own file sections.
// Plan: .claude/plans/aggregator_file_refs.md §4.1, §4.6 (contract), §4.7.
//
// The aggregator writes `[F3·S7]` (section 7 of the conversation's file WI-3)
// or `[F3]` (WI-3 shown whole) inside the report body. The number after `F` is
// the file's fixed WI number in the conversation (`workspace_items.wi_seq`, plan
// D12 — no separate count); `S7` is the section number the «الخيوط» squares
// show. The backend reads those tags after
// the run and stores one row per distinct ref (`workspace_item_file_refs`, which
// records which report links to which file and section); these types mirror
// `GET /workspace/{item_id}/file-refs` and
// `GET /workspace/{item_id}/file-refs/{ref_id}/section`.
//
// The grammar mirrors `shared/workspace/file_refs.py` exactly.
//
// The tags always carry a letter, so they never collide with a library `[n]`
// (`citations.ts::CITATION_RE` is digits only).
//
// Pure TypeScript — shared with mobile (README rules).

// ---------------------------------------------------------------------------
// Wire shapes
// ---------------------------------------------------------------------------

/** One file ref of a report, as `GET /workspace/{item_id}/file-refs` lists it. */
export interface FileRefRow {
  /** Order of first mention in the report body, 1-based (the «من مستنداتك» order). */
  n: number;
  /** Canonical: `"F3.S7"` (a section) or `"F3"` (the whole document); 3 = `wi_seq`. */
  ref_id: string;
  /** The file's WI number in the conversation. */
  wi_seq: number;
  /** `null` = the whole document. */
  section_seq: number | null;
  page_from: number | null;
  page_to: number | null;
  doc_title: string;
  what: string;
  owner: string;
  brought_by: string;
  /** `false` = the attachment was deleted on purpose. */
  available: boolean;
}

export interface WorkspaceItemFileRefsResponse {
  file_refs: FileRefRow[];
}

/** `GET /workspace/{item_id}/file-refs/{ref_id}/section` (410 = the file is gone). */
export interface FileRefSectionResponse extends FileRefRow {
  content_md: string;
  /** The id `/files/{id}` («الملفات») opens; `null` when the file is not listed there. */
  files_item_id: string | null;
}

/** The popup's message for a 410 — the attachment was deleted on purpose. */
export const FILE_REF_GONE_AR = "لم يعد هذا الملف متاحًا";

/** The view copy (chip, popup, «من مستنداتك» list). */
export const FILE_REFS_AR = {
  listTitle: "من مستنداتك",
  sectionLabel: (seq: number) => `القسم ${seq}`,
  wholeDocument: "المستند كاملًا",
  owner: "صاحب القسم",
  broughtBy: "قدّمه",
  openFile: "افتح الملف",
  gone: FILE_REF_GONE_AR,
  loadError: "تعذّر تحميل القسم",
  emptySection: "لا يحتوي هذا القسم على نص",
  /** The chip's accessible name: «القسم 7 من الحكم المرفق» / «الحكم المرفق». */
  chipLabel: (row: Pick<FileRefRow, "section_seq" | "doc_title">) => {
    const title = (row.doc_title ?? "").trim() || "المستند المرفق";
    return row.section_seq == null ? title : `القسم ${row.section_seq} من ${title}`;
  },
} as const;

// ---------------------------------------------------------------------------
// Parsing — mirrors shared/workspace/file_refs.py exactly: any case, `F3` /
// `F-3` / `F 3`, the angle map's separators before `S` plus «،», leading zeros,
// Arabic-Indic digits, several refs in one bracket
// ---------------------------------------------------------------------------

const DIGITS = "0-9\\u0660-\\u0669\\u06F0-\\u06F9";
/** Between `F` and its number (`F3`, `F-3`, `F 3`, `F_3`). */
const F_SEP ="[\\s\\-\\u2010\\u2011\\u2013_]*";
/** refs.py `_SEP` plus «،»: the characters allowed between the file and the section. */
const SEP = "[\\s.·‧•・\\-_/:|,،]*";
/** One ref inside a bracket: `F3·S7` or `F3`. Groups: the WI number, section (optional). */
const REF_BODY = `F${F_SEP}([${DIGITS}]+)(?:${SEP}S\\s*([${DIGITS}]+))?`;
/** Between two refs written in one bracket (`[F3·S7، F3·S9]`). */
const LIST_SEP = "\\s*[,،;؛]\\s*";

/** A whole bracket of file refs, global — `[F3·S7]`, `[f-3-s07]`, `[F2]`, `[F3·S7، F3·S9]`. */
export const FILE_REF_TAG_RE = new RegExp(
  `\\[\\s*${REF_BODY}(?:${LIST_SEP}${REF_BODY})*\\s*\\]`,
  "gi",
);
const REF_ONE_RE = new RegExp(`^\\s*\\[?\\s*${REF_BODY}\\s*\\]?\\s*$`, "i");
const REF_EACH_RE = new RegExp(REF_BODY, "gi");

function asciiInt(value: string): number {
  let out = "";
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if (code >= 0x0660 && code <= 0x0669) out += String(code - 0x0660);
    else if (code >= 0x06f0 && code <= 0x06f9) out += String(code - 0x06f0);
    else out += ch;
  }
  return Number.parseInt(out, 10);
}

/** `"F3.S7"` for a section, `"F2"` for a whole document (F + the file's WI number). */
export function formatFileRef(wiSeq: number, section: number | null): string {
  return section == null ? `F${wiSeq}` : `F${wiSeq}.S${section}`;
}

function fromGroups(wi: string | undefined, sec: string | undefined): string | null {
  const d = asciiInt(wi ?? "");
  if (!Number.isFinite(d) || d <= 0) return null;
  if (sec === undefined) return formatFileRef(d, null);
  const s = asciiInt(sec);
  if (!Number.isFinite(s) || s <= 0) return null;
  return formatFileRef(d, s);
}

/** Any accepted spelling (`F3·S7`, `[f-3-s07]`, `F٣.S٧`, `F2`) → canonical; `null` when not a file ref. */
export function canonicalFileRef(value: string): string | null {
  const m = REF_ONE_RE.exec(String(value ?? ""));
  return m ? fromGroups(m[1], m[2]) : null;
}

/** `{ wiSeq, section }` of a file ref (`section` null = whole document); `null` when not one. */
export function parseFileRef(value: string): { wiSeq: number; section: number | null } | null {
  const canon = canonicalFileRef(value);
  if (!canon) return null;
  const [d, s] = canon.slice(1).split(".S");
  return { wiSeq: Number(d), section: s === undefined ? null : Number(s) };
}

export interface FileRefTag {
  /** Offsets of the whole bracket in the text. */
  start: number;
  end: number;
  /** The bracket as written. */
  raw: string;
  /** Its refs, canonical, in order, without repeats. */
  refs: string[];
}

/** Every file-ref bracket in `text`, in order. Library `[4]` / `[4,17]` never match. */
export function findFileRefTags(text: string): FileRefTag[] {
  const out: FileRefTag[] = [];
  const source = String(text ?? "");
  if (!source.includes("[")) return out;
  const re = new RegExp(FILE_REF_TAG_RE.source, FILE_REF_TAG_RE.flags);
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    const refs: string[] = [];
    const each = new RegExp(REF_EACH_RE.source, REF_EACH_RE.flags);
    let r: RegExpExecArray | null;
    while ((r = each.exec(m[0])) !== null) {
      const canon = fromGroups(r[1], r[2]);
      if (canon && !refs.includes(canon)) refs.push(canon);
    }
    if (refs.length > 0) out.push({ start: m.index, end: m.index + m[0].length, raw: m[0], refs });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Text forms — copy, share view (plan §4.6 "One text form")
// ---------------------------------------------------------------------------

/** `(القسم 7 من {doc_title})`; a whole document `({doc_title})`. */
export function fileRefTextLabel(row: Pick<FileRefRow, "section_seq" | "doc_title">): string {
  const title = (row.doc_title ?? "").trim() || "المستند المرفق";
  return row.section_seq == null ? `(${title})` : `(القسم ${row.section_seq} من ${title})`;
}

/**
 * The body with each file-ref tag as plain text: a ref with a row → its
 * {@link fileRefTextLabel}; a ref with no row → removed (with the spaces before
 * it). `rows === undefined` → the body unchanged.
 */
export function fileRefsToText(body: string, rows: readonly FileRefRow[] | undefined): string {
  if (rows === undefined) return body;
  const text = String(body ?? "");
  const tags = findFileRefTags(text);
  if (tags.length === 0) return text;
  const byRef = new Map(rows.map((r) => [canonicalFileRef(r.ref_id) ?? r.ref_id, r]));
  let out = "";
  let last = 0;
  let prevLabelEnd = -1;
  for (const tag of tags) {
    const labels = tag.refs
      .map((ref) => byRef.get(ref))
      .filter((r): r is FileRefRow => r !== undefined)
      .map(fileRefTextLabel);
    let before = text.slice(last, tag.start);
    if (labels.length === 0) {
      before = before.replace(/[ \t ]+$/, "");
      out += before;
    } else {
      // Two tags side by side (`[F3·S7][F3·S9]`) read as two labels, not one word.
      out += before + (prevLabelEnd === tag.start ? " " : "") + labels.join(" ");
      prevLabelEnd = tag.end;
    }
    last = tag.end;
  }
  return out + text.slice(last);
}

/** The body with every file-ref tag removed — the public share view. */
export function stripFileRefTags(body: string): string {
  return fileRefsToText(body, []);
}

/** The rows in `n` order (the «من مستنداتك» list order). */
export function orderFileRefs(rows: readonly FileRefRow[]): FileRefRow[] {
  return [...rows].sort((a, b) => a.n - b.n);
}
