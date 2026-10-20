// قوالبي (user templates) — the composer's «قالب» pick, shared by the web and
// the mobile app.
//
// Additive: the web still declares `UserTemplate`, `TemplateListResponse` and
// `PendingTemplate` in `@/types`, and builds the directive line inline in
// `components/chat/ChatInput.tsx` (handleSend). The shapes and the line are
// identical; the web can switch to these whenever it next touches those files.

import type { WorkspaceCreator } from "./agents";
import type { TemplateScope } from "./messages";

/** One row of `GET /api/v1/templates` (قوالبي). */
export interface UserTemplate {
  template_id: string;
  /** `null` for system templates (قالب عام). */
  user_id: string | null;
  title: string;
  content_md: string;
  /** `"system"` for system templates. */
  created_by: WorkspaceCreator | "system";
  metadata: Record<string, unknown>;
  scope: TemplateScope;
  subtype: string | null;
  court: string | null;
  /** Absolute http(s) URLs the template was derived from. */
  sources: string[];
  created_at: string | null;
  updated_at: string | null;
}

/** `GET /api/v1/templates[?q=]` — newest-updated first, or BM25-ranked with `q`. */
export interface TemplateListResponse {
  templates: UserTemplate[];
}

/**
 * قالب picked in the composer — ONE at a time (the planner drafts from a single
 * template); picking another replaces it. Purely client-side: no id is sent,
 * the send carries the directive line below instead.
 */
export interface PendingTemplate {
  templateId: string;
  title: string;
}

/** `GET /templates?q=` answers a shorter term with a 400 — never send one. */
export const TEMPLATE_SEARCH_MIN_CHARS = 3;

/** The row label of a template in the picker (web TemplateMenuList). */
export function templateMenuLabel(title: string): string {
  return title.trim() || "قالب بدون عنوان";
}

/** The chip label of the picked template (web TemplateChip). */
export function templateChipLabel(title: string): string {
  return title.trim() || "قالب";
}

/** `استخدم القالب: «…»` — the writer_planner matches this title verbatim in
 *  its `<my_templates>` block and sets `chosen_template`. */
export function templateDirectiveLine(title: string): string {
  return `استخدم القالب: «${title.trim()}»`;
}

/**
 * The outgoing message text for a send with a template chip — exactly the web
 * ChatInput.handleSend: the trimmed text and the directive line, joined by a
 * blank line (just the line when the text is empty). Without a template the
 * text is returned untouched.
 */
export function withTemplateDirective(text: string, template: Pick<PendingTemplate, "title"> | null): string {
  if (!template) return text;
  return [text.trim(), templateDirectiveLine(template.title)].filter(Boolean).join("\n\n");
}
