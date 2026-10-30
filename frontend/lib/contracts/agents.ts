// Agents, workspace items and resumable uploads — moved verbatim from
// frontend/types/index.ts (the web re-exports them from `@/types`).

// ==========================================
// RESUMABLE UPLOADS
// ==========================================

/** Server response from `/cases/{id}/documents/init` and the workspace twin. */
export interface UploadInitResponse {
  /** Present on document init. */
  document_id?: string;
  /** Present on workspace-attachment init. */
  item_id?: string;
  storage_path: string;
  bucket: string;
  upload_url: string;
  expires_at: string;
}

// ==========================================
// AGENTS & WORKSPACE ITEMS
// ==========================================

export type AgentFamily = 'deep_search' | 'writing' | 'memory' | 'router';

export type TaskType = 'deep_search' | 'writing';

export type WorkspaceItemKind =
  | 'attachment'
  | 'note'
  | 'agent_search'
  | 'agent_writing'
  | 'convo_context'
  | 'references';

export type WorkspaceCreator = 'user' | 'agent';

/** A user's 👍/👎 rating on a workspace item. ``null`` = no rating. */
export type WorkspaceFeedback = 'up' | 'down' | null;

/** Free-form subtype string carried in metadata.subtype — drives chip color/icon. */
export type WorkspaceItemSubtype =
  | 'report'
  | 'contract'
  | 'memo'
  | 'summary'
  | 'memory_file'
  | 'legal_opinion'
  | 'legal_synthesis'
  | (string & {});

export interface WorkspaceItem {
  item_id: string;
  user_id: string;
  conversation_id: string | null;
  case_id: string | null;
  message_id?: string | null;
  /**
   * Migration 052: conversation-scoped 1-based alias. The router and the
   * planners address items as «WI-{wi_seq}» and say that alias out loud in chat
   * («حفظت النص … (WI-1)»), so the UI renders it via ``WiBadge`` — otherwise the
   * reader has no way to tell which card the sentence points at. ``null`` for
   * items with no conversation home; ``undefined`` on responses from a backend
   * older than this field.
   */
  wi_seq?: number | null;
  agent_family: AgentFamily | null;
  kind: WorkspaceItemKind;
  created_by: WorkspaceCreator;
  title: string;
  content_md: string | null;
  storage_path: string | null;
  document_id: string | null;
  is_visible: boolean;
  /** User 👍/👎 rating: 'up' | 'down' | null (no rating). Migration 073. */
  feedback: WorkspaceFeedback;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  /**
   * WI preview lock (.claude/plans/wi_preview_lock.md) — ADDED, optional. Sent
   * ONLY on a locked item (a free user's agent report / draft created after
   * launch); absent everywhere else, and always while the server flag is off.
   * When present, `content_md` is the visible part followed by
   * `WI_PREVIEW_LEGACY_SUFFIX` — read the body through
   * `wiPreviewVisibleContent()` (./wi-preview), never raw.
   */
  preview?: WorkspaceItemPreview | null;
}

/** `WorkspaceItem.preview` — mirror of backend `WorkspaceItemPreview`. */
export interface WorkspaceItemPreview {
  locked: boolean;
  /** Characters of the visible part (without the legacy line). */
  shown_chars: number;
  /** Characters of the whole stored body. */
  total_chars: number;
  /** Used citations that only the hidden part cites. */
  hidden_ref_count: number;
}

export interface WorkspaceItemListResponse {
  items: WorkspaceItem[];
  total: number;
}

// ------------------------------------------
// Workspace write bodies — ADDED (identical shapes to `frontend/types/index.ts`,
// which the web keeps declaring for itself), for the mobile workspace screen.
// ------------------------------------------

/** `POST /conversations/{id}/workspace/notes` (and `/references`). */
export interface CreateNoteRequest {
  title: string;
  content_md?: string;
}

export type CreateReferenceRequest = CreateNoteRequest;

/** `PATCH /workspace/{id}/visibility` — `false` keeps the item out of the
 *  agents' workspace context (the router never sees it). */
export interface UpdateVisibilityRequest {
  is_visible: boolean;
}

/** `GET /workspace/{id}/file` — short-lived signed URL of an attachment's original. */
export interface WorkspaceFileUrlResponse {
  url: string;
  expires_at: string;
}
