import { TEMPLATE_SEARCH_MIN_CHARS, type TemplateListResponse } from "@contracts";

import { apiFetch } from "./client";

// قوالبي — backend/app/api/templates.py (web `templatesApi.list`).

/** Newest-updated first; with a term of 3+ characters, BM25-ranked over title
 *  + content. A shorter term is dropped (the server answers it with a 400). */
export function listTemplates(q = ""): Promise<TemplateListResponse> {
  const term = q.trim();
  const suffix = term.length >= TEMPLATE_SEARCH_MIN_CHARS ? `?q=${encodeURIComponent(term)}` : "";
  return apiFetch<TemplateListResponse>(`/templates${suffix}`);
}
