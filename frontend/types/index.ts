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

export interface RegisterResponse {
  user: User;
  verification_sent: boolean;
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

export interface PendingFile {
  id: string;
  file: File;
  previewUrl: string;
  name: string;
  size: number;
  mimeType: string;
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

export interface SSEDone {
  message_id: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
  };
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
// agents/deep_search_v4/source_viewer.py::SourceView. Persisted on an
// `agent_search` workspace item as `metadata.references` and rendered by
// the workspace ReferencePanel.

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
      details_url: string;
    }
  | {
      source_type: 'gov_service';
      title: string;
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
}

/** Typed view of `metadata` on an `agent_search` workspace item. */
export interface AgentSearchMetadata {
  subtype?: string;
  references?: Reference[];
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

// ==========================================
// SEND MESSAGE PAYLOAD
// ==========================================

export interface SendMessagePayload {
  content: string;
  attachment_ids?: string[] | null;
}
