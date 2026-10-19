// Agent-output actions shared by the web and the mobile app: publish by link
// (مشاركة / حفظ كمدونة), edit a draft, and the PDF / Word export.
//
// Additive: the web still declares `ShareDraftResponse`, `ShareArtifactRequest`,
// `ShareArtifactResponse` and `UpdateWorkspaceItemRequest` in `@/types`, and
// `ExportFormat` in `@/lib/api`. The shapes are identical; the web can switch
// to re-exporting these whenever it next touches those files.

/** Response of `GET /api/v1/workspace/{item_id}/share-draft` — pre-fills the
 *  share sheet. */
export interface ShareDraftResponse {
  /** Derived default السؤال (preceding user message / artifact title). */
  default_question: string;
  /** Derived default مدونة title (the artifact's own `title`); may be null. */
  default_title: string | null;
}

/** Which public template a share publishes: السؤال page vs editorial article. */
export type ShareDisplayMode = "question" | "title";

/** Request body of `POST /api/v1/workspace/{item_id}/share`. */
export interface ShareArtifactRequest {
  /** Final السؤال text (verbatim); "" in title mode. */
  question_text: string;
  display_mode: ShareDisplayMode;
  /** Final مدونة title (title mode); null in question mode. */
  title: string | null;
}

/** Response of `POST /api/v1/workspace/{item_id}/share`. */
export interface ShareArtifactResponse {
  /** Unguessable slug used in the public URL. */
  token: string;
  /** Fully-qualified `/blog/{token}` URL, built server-side. */
  public_url: string;
}

/** Body of `PATCH /api/v1/workspace/{item_id}` (note / agent_writing edit). */
export interface UpdateWorkspaceItemRequest {
  title?: string;
  content_md?: string;
}

/** `POST /api/v1/export` formats. */
export type ExportFormat = "pdf" | "docx";

/** Body of `POST /api/v1/export` — the response is the binary file. */
export interface ExportDocumentRequest {
  format: ExportFormat;
  /** Document title (server H1 + Content-Disposition), ≤ 200 chars. */
  title: string;
  markdown: string;
}
