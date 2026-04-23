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

export interface Message {
  message_id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  model?: string;
  attachments: Attachment[];
  created_at: string;
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

export interface SSECitations {
  articles: Citation[];
}

export interface Citation {
  article_id: string;
  law_name: string;
  article_number: number;
  relevance_score?: number;
}

export interface SSEDone {
  message_id: string;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
  };
}

// ==========================================
// AGENTS & ARTIFACTS
// ==========================================

export type AgentFamily = 'deep_search' | 'end_services' | 'extraction' | 'memory' | 'router';

export type TaskType = 'deep_search' | 'end_services' | 'extraction';

export type ArtifactType = 'report' | 'contract' | 'memo' | 'summary' | 'memory_file' | 'legal_opinion';

export interface Artifact {
  artifact_id: string;
  user_id: string;
  conversation_id: string | null;
  case_id: string | null;
  agent_family: AgentFamily;
  artifact_type: ArtifactType;
  title: string;
  content_md: string;
  is_editable: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ArtifactListResponse {
  artifacts: Artifact[];
  total: number;
}

// ==========================================
// PREFERENCES & TEMPLATES
// ==========================================

export interface UserPreferences {
  user_id: string;
  preferences: Record<string, unknown>;
}

export interface UserTemplate {
  template_id: string;
  user_id: string;
  title: string;
  description: string;
  prompt_template: string;
  agent_family: AgentFamily;
  is_active: boolean;
  created_at: string;
}

export interface TemplateListResponse {
  templates: UserTemplate[];
  total: number;
}

// ==========================================
// SSE EVENTS (Agent)
// ==========================================

export interface SSEArtifactCreated {
  artifact_id: string;
  artifact_type: ArtifactType;
  title: string;
}

export interface SSEAgentSelected {
  agent_family: AgentFamily;
}

export interface SSETaskStarted {
  task_id: string;
  task_type: TaskType;
}

export interface SSETaskEnded {
  task_id: string;
  summary: string;
}

export interface SSEArtifactUpdated {
  artifact_id: string;
}

// ==========================================
// SEND MESSAGE PAYLOAD
// ==========================================

export interface SendMessagePayload {
  content: string;
  task_type?: TaskType | null;
  attachment_ids?: string[] | null;
}
