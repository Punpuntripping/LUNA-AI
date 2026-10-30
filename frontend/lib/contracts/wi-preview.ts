// WI preview lock — the half the web and the app share (.claude/plans/wi_preview_lock.md).
//
// The SERVER decides and truncates (shared/workspace/preview_lock.py): a free
// user's agent report / draft created after launch arrives with ~40% of its
// body, a `preview` block, and one fixed Arabic line appended for clients that
// predate this file. Clients only strip that line, render the wall, and go
// read-only (no edit, share, save-as-blog or export; copying the visible part
// stays). This file is the ONE place the wall's Arabic copy lives, so both
// apps say the same words.

import type { Reference } from "./references";
import type { WorkspaceItem, WorkspaceItemPreview } from "./agents";

/**
 * The line the server appends after the visible part (byte-identical to
 * `LEGACY_WALL_LINE` in shared/workspace/preview_lock.py — a backend test pins
 * the two). Old builds render it as the end of the document; new clients
 * strip it and show `WiPreviewWall` instead.
 */
export const WI_PREVIEW_LEGACY_LINE = "> **بقية هذا المستند متاحة في الباقات المدفوعة.**";
export const WI_PREVIEW_LEGACY_SUFFIX = "\n\n" + WI_PREVIEW_LEGACY_LINE;

/** Analytics `gate_kind` of the wall (backend GATE_KINDS, web GateKind). */
export const WI_PREVIEW_GATE_KIND = "wi_preview" as const;

/**
 * PROPOSED wall copy (owner to confirm). Neutral on purpose — it states what
 * the free plan shows and never tells the reader to buy elsewhere, so the app
 * can show it verbatim under the store rules. The web adds the `cta` button;
 * the app shows it only when its in-app purchase entry is visible.
 */
export const WI_PREVIEW_COPY = {
  title: "بقية هذا المستند متاحة في الباقات المدفوعة",
  body:
    "في الباقة المجانية تظهر بداية تقارير الباحث ومسودات الكاتب فقط. " +
    "النص الكامل، مع جميع مراجعه ومشاركته وتصديره، متاح في الباقات المدفوعة.",
  cta: "عرض الباقات",
} as const;

/** `GET /workspace/{id}/references` — `hidden_count` only on a locked item. */
export interface WorkspaceItemReferencesResponse {
  references: Reference[];
  hidden_count?: number;
}

type PreviewCarrier = Pick<WorkspaceItem, "content_md"> & {
  preview?: WorkspaceItemPreview | null;
};

/** True when the server sent this item as a preview. */
export function isWiPreviewLocked(item: PreviewCarrier | null | undefined): boolean {
  return !!item?.preview?.locked;
}

/**
 * The body to render, copy or count: `content_md` without the legacy line on
 * a locked item, `content_md` unchanged otherwise (`""` for null).
 */
export function wiPreviewVisibleContent(item: PreviewCarrier | null | undefined): string {
  const content = item?.content_md ?? "";
  if (!isWiPreviewLocked(item)) return content;
  return content.endsWith(WI_PREVIEW_LEGACY_SUFFIX)
    ? content.slice(0, content.length - WI_PREVIEW_LEGACY_SUFFIX.length)
    : content;
}

/** Shown share of the body, whole percent (0 when unknown). */
export function wiPreviewShownPct(preview: WorkspaceItemPreview | null | undefined): number {
  if (!preview || !(preview.total_chars > 0)) return 0;
  return Math.round((100 * preview.shown_chars) / preview.total_chars);
}

/**
 * «وN مصادر أخرى في الجزء المقفل» with Arabic number agreement and Latin
 * digits; `""` for 0. Mirrors `hidden_refs_line_ar` in
 * shared/workspace/preview_lock.py (the router's source list says the same).
 */
export function wiPreviewHiddenRefsLine(count: number): string {
  const n = Math.max(0, Math.floor(Number.isFinite(count) ? count : 0));
  if (n === 0) return "";
  if (n === 1) return "ومصدر آخر في الجزء المقفل";
  if (n === 2) return "ومصدران آخران في الجزء المقفل";
  if (n <= 10) return `و${n} مصادر أخرى في الجزء المقفل`;
  return `و${n} مصدراً آخر في الجزء المقفل`;
}
