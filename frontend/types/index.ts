// ==========================================
// USER & AUTH
// ==========================================

export interface User {
  user_id: string;
  email: string;
  full_name_ar?: string | null;
  subscription_tier?: string | null;
  created_at?: string | null;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

// ==========================================
// CASES
// ==========================================

export type CaseType = "عقاري" | "تجاري" | "عمالي" | "جنائي" | "أحوال_شخصية" | "إداري" | "تنفيذ" | "عام";
export type CaseStatus = "active" | "closed" | "archived";
export type CasePriority = "high" | "medium" | "low";

export interface CaseSummary {
  case_id: string;
  case_name: string;
  case_type: CaseType;
  status: CaseStatus;
  priority: CasePriority;
  description?: string | null;
  case_number?: string | null;
  court_name?: string | null;
  conversation_count: number;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface CaseDetail extends CaseSummary {
  parties?: Record<string, unknown> | null;
}

export interface CaseStats {
  total_conversations: number;
  total_documents: number;
  total_memories: number;
}

export interface CaseListResponse {
  cases: CaseSummary[];
  total: number;
  page: number;
  per_page: number;
}

export interface CreateCaseRequest {
  case_name: string;
  case_type: CaseType;
  description?: string;
  case_number?: string;
  court_name?: string;
  priority?: CasePriority;
}

export interface CreateCaseResponse {
  case: CaseDetail;
  first_conversation_id: string;
}

export interface CaseDetailResponse {
  case: CaseDetail;
  conversations: ConversationSummary[];
  stats: CaseStats;
}

// ==========================================
// CONVERSATIONS
// ==========================================

export interface ConversationSummary {
  conversation_id: string;
  case_id?: string | null;
  title_ar?: string | null;
  message_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  model_name?: string | null;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
  has_more: boolean;
}

export interface CreateConversationRequest {
  case_id?: string | null;
}

// ==========================================
// MESSAGES
// ==========================================

/** Discriminator for messages tied to an agent ask_user pause/resume cycle. */
export type MessageMetadataKind = 'agent_question' | 'agent_answer';

export interface MessageMetadata {
  /** When set, this message is part of an agent ask_user turn. */
  kind?: MessageMetadataKind;
  /** The agent_run that originated the question / is being answered. */
  run_id?: string;
  /** The agent family that paused for input. */
  agent_family?: AgentFamily;
  /** Optional suggested replies surfaced with an agent_question. */
  suggestions?: string[];
  [key: string]: unknown;
}

export interface Message {
  message_id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  model?: string;
  attachments: Attachment[];
  created_at: string;
  metadata?: MessageMetadata;
  /**
   * Workspace item ids produced by the agent run that authored this message.
   * Populated by the backend on assistant messages whose agent_runs created
   * one or more ``workspace_items``. Always undefined / empty for user or
   * legacy / Q&A / agent_question messages.
   *
   * NOTE (Window C, 2026-05-20): backend does not yet expose this field on
   * ``MessageResponse``; the frontend treats it defensively as possibly
   * undefined so the new citation + chip UI is a no-op until the backend
   * ships it.
   */
  artifact_ids?: string[] | null;
  /**
   * Workspace items the planner pointed back to instead of publishing a new
   * card (Phase E ``build_artifact=False``). Drives the prior-card chip in
   * the assistant bubble. When set, the chat-store entry recorded by the
   * live ``referenced_existing_item`` SSE event still wins for the
   * just-streamed turn; the persisted value lights up the chip on refresh.
   */
  referenced_item_ids?: string[] | null;
  isOptimistic?: boolean;
  isFailed?: boolean;
  isStreaming?: boolean;
}

export interface Attachment {
  id: string;
  document_id: string;
  attachment_type: 'pdf' | 'image' | 'file';
  filename: string;
  file_size?: number;
}

export interface MessageListResponse {
  messages: Message[];
  has_more: boolean;
}

// ==========================================
// DOCUMENTS
// ==========================================

export interface Document {
  document_id: string;
  case_id: string;
  document_name: string;
  mime_type: string;
  file_size_bytes: number;
  extraction_status: 'pending' | 'processing' | 'completed' | 'failed';
  created_at: string;
}

export interface DocumentListResponse {
  documents: Document[];
  total: number;
}

export interface DownloadResponse {
  url: string;
  expires_at: string;
}

// ==========================================
// MEMORIES
// ==========================================

export interface Memory {
  memory_id: string;
  case_id: string;
  memory_type: 'fact' | 'document_reference' | 'strategy' | 'deadline' | 'party_info';
  content_ar: string;
  confidence_score?: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  memories: Memory[];
  total: number;
}

// ==========================================
// PENDING FILES (upload preview)
// ==========================================

/**
 * Upload lifecycle for a single pending file in the chat input.
 *
 *   queued    → file picked, waiting for an in-flight upload slot
 *   uploading → tus PATCH chunks in progress (progress 0..1 valid)
 *   completed → finalize call returned 200; ready to be sent with a message
 *   failed    → init / tus / finalize errored; errorMessage carries the Arabic
 *               reason; retry by re-adding the file
 *   cancelled → user clicked cancel; backend row was soft-deleted
 */
export type AttachmentUploadStatus =
  | "queued"
  | "uploading"
  | "completed"
  | "failed"
  | "cancelled";

export interface PendingFile {
  id: string;
  file: File;
  previewUrl: string;
  name: string;
  size: number;
  mimeType: string;
  /** Lifecycle of the direct-to-Supabase TUS upload. */
  uploadStatus: AttachmentUploadStatus;
  /** Bytes uploaded / total bytes, 0..1. */
  uploadProgress: number;
  /** workspace_items.item_id once /init returns; null before that. */
  itemId: string | null;
  /** Arabic-language error message when uploadStatus === 'failed'. */
  errorMessage: string | null;
}

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
// SSE EVENTS
// ==========================================

export interface SSEMessageStart {
  user_message_id: string;
  assistant_message_id: string;
  conversation_id: string;
}

export interface SSEToken {
  text: string;
}

/**
 * Emitted INSTEAD of message_start when a send is rejected because a pipeline
 * is already running for this conversation (per-conversation in-flight dedup —
 * backend message_service `_active_runs`). No new pipeline is started and no
 * duplicate user message is saved. The client drops its optimistic duplicate
 * and lets the existing in-flight assistant message surface on completion.
 */
export interface SSEDuplicate {
  assistant_message_id: string;
  conversation_id: string;
  detail: string;
}

export interface SSEDone {
  message_id: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
  };
  /**
   * Window B Tasks 5–7: workspace_items produced by the agent run that
   * authored this assistant message. Echoed on the live `done` event so the
   * chip + clickable [n] citations light up immediately, without waiting for
   * the next messages-list refetch. ``null`` when the turn produced no
   * artifact (mock RAG, Q&A, paused agent_question, etc).
   */
  artifact_ids?: string[] | null;
  /**
   * Window B Tasks 5–7: workspace_items the planner referenced instead of
   * publishing a new card (Phase E build_artifact=False branch). Drives the
   * "راجع البطاقة السابقة" chip.
   */
  referenced_item_ids?: string[] | null;
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
  agent_family: AgentFamily | null;
  kind: WorkspaceItemKind;
  created_by: WorkspaceCreator;
  title: string;
  content_md: string | null;
  storage_path: string | null;
  document_id: string | null;
  is_visible: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceItemListResponse {
  items: WorkspaceItem[];
  total: number;
}

// ==========================================
// DEEP_SEARCH ARTIFACT REFERENCES (JSON render object)
// ==========================================
// Mirrors agents/deep_search_v4/aggregator/models.py::Reference and
// agents/deep_search_v4/source_viewer.py::SourceView. Migration 049: refs
// live in the relational `workspace_item_references` table; the backend
// reconstructs this shape on read by joining to source tables. The
// frontend fetches via `useWorkspaceItemReferences(item_id)` and the
// workspace ReferencePanel renders the response.

export type ReferenceDomain = 'regulations' | 'compliance' | 'cases';

export type ReferenceSourceType =
  | 'article'
  | 'section'
  | 'chunk'
  | 'regulation'
  | 'gov_service'
  | 'form'
  | 'case';

/** One resolved cross-reference from a regulation chunk to a target unit. */
export interface CrossRef {
  target_type: string;
  target_reg_title: string;
  target_number: number | null;
  relation: string;
  content: string;
}

/** Click-ready original-source payload — discriminated on `source_type`. */
export type SourceView =
  | {
      source_type: 'chunk';
      title: string;
      content: string;
      regulation_title: string;
      regulation_source_url: string;
      regulation_pdf_link: { url?: string; [k: string]: unknown } | null;
    }
  | {
      source_type: 'case';
      title: string;
      /** Case body (markdown) — rendered above the details_url link. */
      content: string;
      details_url: string;
    }
  | {
      source_type: 'gov_service';
      title: string;
      /** Long-form service title (``services.intro_title``). Often redundant with ``title``. */
      intro_title: string;
      /** One-sentence description (``services.intro_description``). */
      intro_description: string;
      /** Procedural steps; each entry may contain inline markdown (links, emphasis). */
      steps: string[];
      /** Eligibility / pre-conditions. */
      requirements: string[];
      /** Documents the user must submit. */
      required_documents: string[];
      national_platform_url: string;
      service_url: string;
    }
  // Legacy variants — retained for reload of pre-URA-v3.0 artifacts.
  | {
      source_type: 'article' | 'section' | 'regulation';
      title: string;
      content?: string;
      [k: string]: unknown;
    };

/** One numbered citation entry in a deep_search artifact's reference list. */
export interface Reference {
  n: number;
  source_type: ReferenceSourceType;
  domain: ReferenceDomain;
  relevance: 'high' | 'medium';
  regulation_title: string;
  title: string;
  snippet: string;
  ref_id: string;
  article_num?: string | null;
  section_title?: string | null;
  landing_url: string;
  service_url: string;
  url: string;
  details_url: string;
  entity_name: string;
  cross_refs: CrossRef[];
  source_view: SourceView | null;
  /**
   * Writer-publisher attribution: when this reference was projected onto an
   * ``agent_writing`` workspace item from a source research WI, ``source_wi``
   * carries the LLM-facing alias (e.g. ``"WI-1"``) of that source. Lives
   * exclusively in ``workspace_items.metadata.references`` on the writer's
   * output row — NOT in ``workspace_item_references`` rows — so callers that
   * fetch references via ``/workspace/{id}/references`` must overlay it from
   * the item's metadata blob to surface provenance to the lawyer.
   *
   * Always undefined for ``agent_search`` items (no source disambiguation
   * applies — the search agent IS the source).
   */
  source_wi?: string | null;
  /**
   * Writer-publisher attribution: the ``n`` this ref had inside the source WI
   * before the writer renumbered it 1..K in body order. Useful for forensic
   * click-through ("which (n) on WI-1 produced this card?"); same overlay
   * rules as ``source_wi``.
   */
  source_n?: number | null;
}

/**
 * Entry shape inside ``workspace_items.metadata.references`` for an
 * ``agent_writing`` item. Written by ``agents.writer.publisher`` to give the
 * frontend a thin attribution view that maps the writer's body-order ``n``
 * back to (source_wi alias, source ref n, ref_id, domain). The full Reference
 * payload (title, snippet, source_view, …) still lives in the relational
 * ``workspace_item_references`` table and is reconstructed by the existing
 * ``/workspace/{id}/references`` endpoint.
 */
export interface WriterMetadataReferenceView {
  n: number;
  source_wi: string | null;
  source_n: number;
  ref_id: string;
  domain: ReferenceDomain;
}

/**
 * Typed view of `metadata` on an `agent_search` workspace item.
 *
 * Migration 049: ``references`` is NO LONGER carried on the metadata blob.
 * It now lives in the relational ``workspace_item_references`` table and
 * is fetched separately via ``useWorkspaceItemReferences(item_id)``.
 */
export interface AgentSearchMetadata {
  subtype?: string;
  confidence?: 'high' | 'medium' | 'low';
  detail_level?: 'low' | 'medium' | 'high';
  ura_log_id?: string;
  [k: string]: unknown;
}

export interface CreateNoteRequest {
  title: string;
  content_md?: string;
}

export interface CreateReferenceRequest {
  title: string;
  content_md?: string;
}

export interface AttachFromDocumentRequest {
  document_id: string;
}

export interface UpdateVisibilityRequest {
  is_visible: boolean;
}

export interface UpdateWorkspaceItemRequest {
  title?: string;
  content_md?: string;
}

export interface WorkspaceFileUrlResponse {
  url: string;
  expires_at: string;
}

// ==========================================
// PREFERENCES
// ==========================================

export type DetailLevel = "low" | "medium" | "high";

export interface UserPreferencesData {
  detail_level?: DetailLevel;
  [key: string]: unknown;
}

export interface UserPreferences {
  user_id: string;
  preferences: UserPreferencesData;
}

export interface UpdatePreferencesRequest {
  preferences: UserPreferencesData;
}

// ==========================================
// USER TEMPLATES (قوالبي)
// ==========================================
// User-global markdown documents (not tied to any conversation or case).
// Editable with the same UX as a `note` workspace item: edit/preview toggle
// plus debounced autosave. Stored server-side via the /templates REST API.

export interface UserTemplate {
  template_id: string;
  user_id: string;
  title: string;
  content_md: string;
  created_by: WorkspaceCreator;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CreateTemplateRequest {
  title: string;
  content_md?: string;
}

export interface UpdateTemplateRequest {
  title?: string;
  content_md?: string;
}

export interface TemplateListResponse {
  templates: UserTemplate[];
}

// ==========================================
// SSE EVENTS (Agent)
// ==========================================

export interface SSEAgentRunStarted {
  agent_family: AgentFamily;
  subtype?: string | null;
}

export interface SSEAgentRunFinished {
  agent_family: AgentFamily;
}

/** Emitted when an agent pauses to ask the user a question (ask_user tool). */
export interface SSEAgentQuestion {
  type: 'agent_question';
  run_id: string;
  question: string;
  suggestions?: string[];
}

/** Emitted when a paused agent_run resumes after the user replied. */
export interface SSEAgentResumed {
  type: 'agent_resumed';
  run_id: string;
  agent_family: AgentFamily;
}

export interface SSEWorkspaceItemCreated {
  item_id: string;
  kind: WorkspaceItemKind;
  title: string;
  created_by: WorkspaceCreator;
  subtype?: string;
}

export interface SSEWorkspaceItemUpdated {
  item_id: string;
}

export interface SSEWorkspaceItemLocked {
  item_id: string;
  locked_until: string;
}

export interface SSEWorkspaceItemUnlocked {
  item_id: string;
}

/**
 * Phase E (full_redesign §3.4a / §6.3 / §9 O5):
 *
 * Emitted by the orchestrator when the planner's responder concludes that a
 * prior workspace_item already covers the current question and therefore
 * sets ``build_artifact=False`` + ``referenced_item_id=<id>``. No new card
 * is published; the frontend surfaces a chip on the in-flight assistant
 * bubble that the user can click to jump to the existing card.
 */
export interface SSEReferencedExistingItem {
  type: "referenced_existing_item";
  item_id: string;
}

// ==========================================
// SEND MESSAGE PAYLOAD
// ==========================================

export interface SendMessagePayload {
  content: string;
  attachment_ids?: string[] | null;
}
