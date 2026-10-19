import type {
  ExportFormat,
  ShareArtifactRequest,
  ShareDisplayMode,
  ShareDraftResponse,
  UpdateWorkspaceItemRequest,
  WorkspaceFeedback,
} from "@contracts";

// Pure rules behind the agent-output action bar — a port of the web's
// WorkspaceItemActionBar + ShareArtifactDialog + SaveAsBlogDialog + ExportMenu
// + MarkdownDocEditor autosave (frontend/components/workspace/*). No React
// Native here so `npm test` covers it.

export type AgentOutputKind = "agent_search" | "agent_writing";
export type ViewMode = "edit" | "preview";

/** One slot of the bar, in RTL reading order (first = right-most). */
export type BarSlot = "mode" | "share" | "saveBlog" | "copy" | "export" | "like" | "dislike";

export interface BarSpec {
  /** معاينة/تحرير — agent_writing only (agent_search is read-only). */
  toggle: boolean;
  /** مشاركة / حفظ كمدونة render on every agent output; on the shared demo
   *  conversation they render DISABLED (the server refuses them anyway). */
  publishDisabled: boolean;
  /** 👍/👎 — hidden on the demo (`workspace_items.feedback` is ONE shared column). */
  feedback: boolean;
}

/** The web's hover hint on the demo's disabled publish buttons. */
export const DEMO_DISABLED_HINT = "متاح في محادثاتك";

export function barSpec(kind: AgentOutputKind, isDemo: boolean): BarSpec {
  return { toggle: kind === "agent_writing", publishDisabled: isDemo, feedback: !isDemo };
}

/** Web order: معاينة/تحرير · مشاركة · حفظ كمدونة · نسخ · PDF · 👍 · 👎. */
export function barSlots(spec: BarSpec): BarSlot[] {
  const out: BarSlot[] = [];
  if (spec.toggle) out.push("mode");
  out.push("share", "saveBlog", "copy", "export");
  if (spec.feedback) out.push("like", "dislike");
  return out;
}

// --- phone fit ---------------------------------------------------------------
// The web's phone dock (`max-md:`) keeps every label and scrolls sideways when
// the row is wider than the viewport. On a 360–420dp phone the agent_writing
// row is ~540dp, so the app instead drops labels one control at a time —
// longest / least important first — until the row fits. The label stays as the
// accessibilityLabel (and a long-press toast). Order never changes.

/** One step of label-dropping, applied in this order. */
export type BarCompression = "saveBlog" | "share" | "copy" | "export" | "toggleUnselected" | "toggleAll";

const COMPRESSION_ORDER: readonly BarCompression[] = [
  "saveBlog",
  "share",
  "copy",
  "export",
  "toggleUnselected",
  "toggleAll",
];

/** Which labelled controls the bar actually renders (نسخ always does). */
export interface BarLabelled {
  toggle: boolean;
  share: boolean;
  saveBlog: boolean;
  export: boolean;
}

/** The compressions that change THIS bar (each step removes a visible label). */
export function compressionLadder(present: BarLabelled): BarCompression[] {
  return COMPRESSION_ORDER.filter((step) => {
    switch (step) {
      case "saveBlog":
        return present.saveBlog;
      case "share":
        return present.share;
      case "copy":
        return true;
      case "export":
        return present.export;
      case "toggleUnselected":
      case "toggleAll":
        return present.toggle;
    }
  });
}

/** The compressions in force at `level` (0 = every label, as on the web). */
export function compressionsAt(ladder: readonly BarCompression[], level: number): ReadonlySet<BarCompression> {
  return new Set(ladder.slice(0, Math.max(0, level)));
}

/** Next level after a measurement: one more step while the row overflows. */
export function nextFitLevel(level: number, ladderLength: number, contentWidth: number, viewportWidth: number): number {
  return contentWidth > viewportWidth + 0.5 && level < ladderLength ? level + 1 : level;
}

/** `#RRGGBB` → `#RRGGBBAA` (the web's `bg-background/95`); other forms as-is. */
export function hexWithAlpha(colour: string, alpha: number): string {
  if (!/^#[0-9a-f]{6}$/i.test(colour)) return colour;
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255);
  return colour + a.toString(16).padStart(2, "0").toUpperCase();
}

/** Tapping the active thumb clears the rating; the other thumb replaces it. */
export function nextFeedback(current: WorkspaceFeedback, thumb: "up" | "down"): WorkspaceFeedback {
  return current === thumb ? null : thumb;
}

// --- مشاركة / حفظ كمدونة ---------------------------------------------------

export const SHARE_ERRORS = {
  draft: "تعذّر تحميل نص السؤال. حاول مرة أخرى.",
  publish: "تعذّر نشر المستند. حاول مرة أخرى.",
  emptyQuestion: "لا يمكن نشر سؤال فارغ.",
  emptyTitle: "اكتب عنواناً للمقال.",
} as const;

export const SAVE_BLOG_ERRORS = {
  draft: "تعذّر تحميل العنوان. حاول مرة أخرى.",
  save: "تعذّر حفظ المدونة. حاول مرة أخرى.",
  emptyTitle: "اكتب عنواناً.",
} as const;

export type ShareBuild = { ok: true; body: ShareArtifactRequest } | { ok: false; error: string };

/** «نشر ونسخ الرابط»: the request for the chosen template, or the Arabic
 *  validation error (same checks, same order as the web dialog). */
export function buildShareRequest(mode: ShareDisplayMode, question: string, title: string): ShareBuild {
  if (mode === "title") {
    const t = title.trim();
    if (!t) return { ok: false, error: SHARE_ERRORS.emptyTitle };
    return { ok: true, body: { question_text: "", display_mode: "title", title: t } };
  }
  const q = question.trim();
  if (!q) return { ok: false, error: SHARE_ERRORS.emptyQuestion };
  return { ok: true, body: { question_text: q, display_mode: "question", title: null } };
}

/** «حفظ كمدونة» is a title-mode publish with an empty question. */
export function buildSaveBlogRequest(title: string): ShareBuild {
  const t = title.trim();
  if (!t) return { ok: false, error: SAVE_BLOG_ERRORS.emptyTitle };
  return { ok: true, body: { question_text: "", display_mode: "title", title: t } };
}

/** Save-as-blog pre-fill: the derived title, else the default question, else "". */
export function saveBlogDefaultTitle(draft: ShareDraftResponse): string {
  return draft.default_title?.trim() || draft.default_question?.trim() || "";
}

// --- PDF / Word --------------------------------------------------------------

export const EXPORT_FALLBACK_NAME = "مستند";
export const EXPORT_ERROR = "تعذّر تنزيل الملف";
const FILENAME_MAX = 120;
const TITLE_MAX = 200;

export const EXPORT_EXTENSION: Record<ExportFormat, string> = { pdf: ".pdf", docx: ".docx" };
export const EXPORT_MIME: Record<ExportFormat, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

/** File name for a title: characters illegal on any OS → space, ≤ 120. */
export function sanitizeExportFilename(title: string): string {
  const cleaned = title
    .replace(/[/\\:*?"<>|\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, FILENAME_MAX)
    .trim();
  return cleaned || EXPORT_FALLBACK_NAME;
}

/** The `title` sent to `POST /export` (server limit 200). */
export function exportDocTitle(title: string): string {
  return title.trim().slice(0, TITLE_MAX) || EXPORT_FALLBACK_NAME;
}

export function exportFileName(title: string, format: ExportFormat): string {
  return sanitizeExportFilename(title) + EXPORT_EXTENSION[format];
}

// --- تحرير (autosave) --------------------------------------------------------

/** Same debounce as the web editor. */
export const AUTOSAVE_DELAY_MS = 800;

/** Opening a draft: rendered read when it has content, the editor when empty. */
export function initialViewMode(content: string): ViewMode {
  return content.trim().length > 0 ? "preview" : "edit";
}

/** What to PATCH, or `null` when the server already holds `next`. */
export function draftPatch(serverContent: string, next: string): UpdateWorkspaceItemRequest | null {
  return next === serverContent ? null : { content_md: next };
}

/**
 * Take a body that changed OUTSIDE this editor (the agent writing, or another
 * device)? Only if it is not the echo of our own save, and the user has no
 * unsaved keystrokes that adopting it would throw away.
 */
export function shouldAdoptServer(incoming: string, local: string, lastSent: string): boolean {
  return incoming !== lastSent && local === lastSent;
}

/** The 409 banner text when the server sends no Arabic detail. */
export const LOCK_CONFLICT_FALLBACK = "ريحان يحرر هذا الملف الآن، حاول مجدداً";
