// ==========================================
// USER & AUTH
// ==========================================

export interface User {
  user_id: string;
  email: string;
  full_name_ar?: string | null;
  /** Legacy column — superseded by plan_id. */
  subscription_tier?: string | null;
  /** Subscription plan (plans table). null = account not activated yet. */
  plan_id?: string | null;
  created_at?: string | null;
  /** Account deletion grace period (30 days). True → app is gated behind
   *  AccountDeletionPendingScreen until restored. */
  deletion_pending?: boolean;
  deletion_requested_at?: string | null;
  /** Server-computed purge date (requested + 30 days) — never derived client-side. */
  purge_at?: string | null;
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
  /** True when the conversation is starred (pinned to the top of lists). */
  is_starred: boolean;
  /** ISO timestamp the conversation was starred, or null when not starred. */
  starred_at?: string | null;
  /**
   * Trimmed excerpt of the matching message content — set ONLY on search
   * results where the match was in a message body (``match_type === 'message'``).
   * Null for title matches and for non-search listings.
   */
  snippet?: string | null;
  /**
   * Where the search term matched: ``'title'`` (in title_ar), ``'message'``
   * (in a message body, with ``snippet`` populated), or null for non-search
   * listings.
   */
  match_type?: 'title' | 'message' | null;
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
  /**
   * ``workspace_items.kind`` of the attached item (messages API embeds it via
   * the migration-088 FK). Distinguishes an uploaded file (``attachment``)
   * from an item attached from within the conversation (e.g. a blog import =
   * ``agent_search``) on the message chips. Optional: absent on legacy cache
   * entries — the chip falls back to ``artifactLookup``.
   */
  kind?: WorkspaceItemKind;
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

/**
 * A blog share-link pasted into the composer, shown as a chip alongside file
 * attachments (.claude/plans/blog_import.md §D4). Imported into the
 * conversation as a ``kind='agent_search'`` workspace item at paste time
 * (mirrors the pre-send upload model); on send the ``itemId`` joins
 * ``attachment_ids``.
 */
export interface PendingBlog {
  /** Chip id (client-generated). */
  id: string;
  /** The 32-hex blog token extracted from the pasted URL. */
  token: string;
  /** Blog/note title once the import (or title fetch) resolves. */
  title: string | null;
  /** ``loading`` = import in flight; ``failed`` chips never block send. */
  status: "loading" | "ready" | "failed";
  /** workspace_items.item_id of the imported note; null until ready. */
  itemId: string | null;
  /**
   * True when THIS chip's import created the note (vs. reusing an existing
   * one via server dedup). Removing the chip deletes the note only then —
   * never a note the user created earlier on purpose.
   */
  createdByChip: boolean;
  /** Arabic-language error message when status === 'failed'. */
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

/**
 * Emitted INSTEAD of message_start when the per-user quota gate rejects the
 * send (shared/quota gate fires before OCR + router). The user message is
 * already persisted; no assistant placeholder is created and the stream ends
 * immediately. ``meter`` is which counter tripped ("plan" = account not
 * activated yet), ``period`` is the window ("session" = rolling 5h),
 * ``resets_at`` is ISO-8601 UTC (empty for plan-inactive). ``message_ar`` is
 * a pre-rendered Arabic string the banner can show verbatim. Point limits:
 * 1 USD = 100 points.
 */
export interface SSEQuotaExceeded {
  meter: "ocr" | "ord" | "web" | "plan";
  period: "session" | "daily" | "weekly" | "monthly" | "none";
  used: number;
  limit: number;
  resets_at: string;
  message_ar: string;
}

/** One progress bar in the Settings → حدود الاستخدام dialog.
 *  ``limit: null`` = unlimited (no cap on this window). */
export interface UsageBar {
  used: number;
  limit: number | null;
  pct: number;
  /** Recovery time (oldest-in-window + window length). Null when used === 0 —
   *  the window is fully available, so there is no countdown to show. */
  resets_at: string | null;
  approximate?: boolean;
}

/** Subscription plan block on the usage report. */
export interface UsagePlan {
  plan_id: string;
  name_ar: string | null;
  expires_at: string | null;
  expired: boolean;
  /** After expiry fallback — expired time-boxed plans resolve to "free". */
  effective_plan_id: string;
  effective_name_ar: string | null;
}

/**
 * GET /api/v1/usage payload. Points (1 USD = 100 points) are gated on a fixed
 * 5-hour session (anchored at the user's first message, resets 5h later) + a
 * rolling 7-day weekly window; ocr is gated on a rolling-30-day window.
 * ``points.monthly`` is enforced as a silent backstop and always null here
 * (never shown). ``locked: true`` → no plan assigned; plan is null and all bars
 * are null.
 */
export interface UsageReport {
  locked: boolean;
  plan: UsagePlan | null;
  points: {
    session: UsageBar | null;
    weekly: UsageBar | null;
    monthly: UsageBar | null; // always null — enforced but not surfaced
  };
  ocr: { monthly: UsageBar | null };
  web: { monthly: UsageBar | null };
  /**
   * «فتح المصادر» — the library unlock allowance (access tiers Phase B). Counts
   * WEIGHTED unlocks (SUM of `library_unlocks.cost`) for the current period, so
   * one نظام can cost more than one مصدر. Optional on the type so a frontend
   * built against a backend that predates the field still compiles and simply
   * hides the bar.
   */
  library?: { period: UsageBar | null };
}

/**
 * The library («فتح المصادر») bar is a `UsageBar` consumer with ONE deliberate
 * difference from points/OCR: its `resets_at` is present even at zero usage.
 * Points and OCR ride ROLLING windows anchored on the oldest spend, so with
 * nothing spent there is no meaningful countdown and the backend sends `null`
 * («متاحة بالكامل»). The library period is a FIXED calendar/subscription window
 * (free → first instant of next UTC month; paid → started_at + n×duration_days),
 * so its boundary is real from the first moment of the period — it is what
 * «يتجدّد رصيدك في …» renders on an untouched account.
 *
 * `limit: null` = unlimited (dev) · `limit: 0` = locked account, no plan yet.
 */
export type LibraryUsageBar = UsageBar;

/** POST /api/v1/plans/redeem success payload — the plan the code granted. */
export interface RedeemCodeResponse {
  plan_id: string;
  name_ar: string | null;
  /** ISO timestamp the granted plan expires, or null for non-expiring plans. */
  expires_at: string | null;
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

export type ReferenceDomain = 'regulations' | 'compliance' | 'cases' | 'circulars';

export type ReferenceSourceType =
  | 'article'
  | 'section'
  | 'chunk'
  | 'regulation'
  | 'gov_service'
  | 'form'
  | 'case'
  | 'circular';

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
  | {
      source_type: 'circular';
      title: string;
      /** Issuing authority name (``entities.name``). */
      entity_name: string;
      /**
       * FULL circular body (markdown) — uncapped. Can be extremely long
       * (up to ~168k chars for outliers); the source-view dialog scrolls it
       * inside its own ``overflow-y-auto`` container rather than growing the
       * layout.
       */
      content: string;
      /** Circular reference id (``circulars.circ_ref``). */
      circ_ref: string;
      /** Optional external source link (``circulars.source``); may be "". */
      url: string;
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
  /**
   * `regulations_v2.doc_type_raw` — the document's own Arabic type
   * (لائحة / تنظيم / دليل / مواصفة قياسية / …). Rendered as the card's type
   * chip in place of the blanket نظام label. Empty string (or absent, on
   * pre-existing snapshots) when the corpus has no determined type — the
   * panel then falls back to `DOMAIN_META.regulations.label`. Regulations
   * domain only.
   */
  doc_type?: string;
  cross_refs: CrossRef[];
  /**
   * ACCESS-TIERS PHASE C (§6.2 step 1): **always `null` on the list payload.**
   * Source bodies left the references list — the full body is metered content
   * now and is fetched one item at a time from
   * ``GET /workspace/{item_id}/references/{n}/source`` after ``resolve_access``.
   * The key is retained on the wire so an un-migrated client degrades to "no
   * reveal button" instead of crashing on a missing property.
   *
   * ⚠ NEVER branch on this to decide whether a source exists — branch on
   * ``has_source``. This field only ever carries a body for a caller that
   * explicitly asked the backend for one (none do today).
   */
  source_view: SourceView | null;
  /**
   * ACCESS-TIERS PHASE C: a full source view CAN be built for this ``n``.
   *
   * THIS is what decides whether «عرض المصدر» renders and whether a ``[n]``
   * click opens the reveal dialog. It costs no request to learn (a probe would
   * be either a charge or a free oracle), and it is false for references whose
   * source row could not be reconstructed (stub cards) — and for every
   * reference on an anonymous blog snapshot, whose frozen bodies the backend
   * strips on read.
   */
  has_source: boolean;
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

export interface UpdateFeedbackRequest {
  feedback: WorkspaceFeedback;
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
// BLOG / PUBLIC SHARE-BY-LINK (مدونة)
// ==========================================
// Snapshot model: at publish time the backend freezes the artifact's
// ``content_md`` + the fully-resolved ``Reference[]`` into a ``blog_posts``
// row. The public page reads only that snapshot — no anon access to live
// workspace data, survives later edits/deletes of the source artifact.

/**
 * Public read shape returned by ``GET /api/v1/public/blog/{token}`` — no auth.
 * Rendered by ``PublicAnswerView``. ``references`` reuses the existing
 * relational ``Reference`` type so ``ReferencePanel`` renders identically.
 */
export interface BlogPostPublic {
  /** السؤال shown on the page (the edited triggering user message). */
  question_text: string;
  /** Page heading + OG title. May be null (fall back to question_text). */
  title: string | null;
  /** Snapshot of the artifact body (markdown). */
  content_md: string;
  /** Snapshot of the resolved citation list. */
  references: Reference[];
  /** e.g. ``legal_synthesis`` → "تحليل قانوني". */
  subtype: string | null;
  /** ISO timestamp of publication. */
  created_at: string;
  /**
   * Share template the publisher chose:
   *   ``"question"`` → السؤال block → answer → references (default layout).
   *   ``"title"``    → editorial blog article (centered hero title + TOC).
   */
  display_mode: "question" | "title";
}

/**
 * Response of ``GET /api/v1/workspace/{item_id}/share-draft`` — pre-fills the
 * share dialog's editable question textarea.
 */
export interface ShareDraftResponse {
  /** Derived default السؤال (preceding user message / artifact title). */
  default_question: string;
  /** Derived default مدونة title (the artifact's own ``title``); may be null. */
  default_title: string | null;
}

/** Request body of ``POST /api/v1/workspace/{item_id}/share``. */
export interface ShareArtifactRequest {
  /** Final السؤال text the publisher chose (verbatim). */
  question_text: string;
  /** Which share template to publish: السؤال page vs editorial article. */
  display_mode: "question" | "title";
  /** Final مدونة title (title mode); null for question mode. */
  title: string | null;
}

/**
 * One card in the public ``/blog`` gallery — a lightweight listing row for a
 * publicly-curated (``is_public``) post. The full body is NOT included; the
 * card links to the public ``/blog/<token>`` page which carries the snapshot.
 */
export interface BlogCardPublic {
  /** Unguessable slug used in the public URL. */
  token: string;
  /** مدونة article title shown on the card. */
  title: string | null;
  /** Plain-text preview derived server-side from ``content_md``. */
  snippet: string;
  /** e.g. ``legal_synthesis`` → "تحليل قانوني". */
  subtype: string | null;
  /** Number of times the public post page has been opened. */
  view_count: number;
  /** ISO timestamp of publication. */
  created_at: string;
}

/**
 * Response of ``GET /api/v1/public/blogs`` — the anonymous public gallery
 * listing (``is_public`` posts, newest first). No auth; SEO-indexable.
 */
export interface PublicBlogsResponse {
  posts: BlogCardPublic[];
}

/**
 * One row in مدوناتي — an owner-scoped listing of the user's own blog_posts
 * (both templates), badged by ``display_mode`` and ``is_public``. Returned by
 * ``GET /api/v1/blogs/mine``.
 */
export interface MyBlogItem {
  post_id: string;
  token: string;
  title: string | null;
  snippet: string;
  subtype: string | null;
  display_mode: "question" | "title";
  is_public: boolean;
  /**
   * True when the row is a snapshot copy imported from someone else's share
   * link («+» in مدوناتي) rather than authored by the caller. Badged
   * «مستوردة» in the lists.
   */
  is_imported: boolean;
  created_at: string;
}

/**
 * Response of ``POST /api/v1/blogs/import`` — save a pasted share link into
 * مدوناتي as a snapshot copy. ``already_saved`` = the caller already held a
 * live post for the same root (authored or previously imported); ``post`` is
 * that existing row.
 */
export interface ImportBlogResponse {
  post: MyBlogItem;
  already_saved: boolean;
}

/**
 * Response of ``POST /api/v1/conversations/{id}/blog-items`` — copy a blog
 * snapshot into the conversation as a ``kind='agent_search'`` workspace item
 * (تحليل قانوني with a real المراجع panel; «اتحدث مع المدونة» / composer
 * paste-chip). ``already_attached`` = a live import for this root post
 * already existed in the conversation.
 */
export interface BlogItemResponse {
  item: WorkspaceItem;
  already_attached: boolean;
}

/**
 * Response of ``GET /api/v1/blogs/mine`` — the owner-scoped مدوناتي listing.
 * ``can_publish_public`` reflects ``users.can_access_blog`` (the curate gate);
 * the management page shows the نشر في المدونة العامة toggle only when true.
 */
export interface MyBlogsResponse {
  can_publish_public: boolean;
  posts: MyBlogItem[];
}

/** Response of ``POST /api/v1/workspace/{item_id}/share``. */
export interface ShareArtifactResponse {
  /** Unguessable slug used in the public URL. */
  token: string;
  /** Fully-qualified ``/blog/{token}`` URL, built server-side. */
  public_url: string;
}

// ==========================================
// PREFERENCES
// ==========================================

export type DetailLevel = "low" | "medium" | "high";

export interface UserPreferencesData {
  detail_level?: DetailLevel;
  /**
   * وضع السرية — reversible identifier masking before user text reaches any
   * external LLM. Default-ON: absent/undefined is treated as `true` by the
   * store's coercion (see `preferences-store.ts`). PATCHed through the same
   * JSONB `/preferences` endpoint as `detail_level`.
   */
  privacy_masking?: boolean;
  /**
   * «اتعرف على ريحان» first-run tour. Absent/false → the tour opens once
   * after login; any dismissal PATCHes it to true. Same JSONB `/preferences`
   * endpoint as the other keys — no backend change needed.
   */
  onboarding_seen?: boolean;
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

/**
 * قالب picked from the composer's «+» menu, shown as a removable chip above
 * the input (the قوالبي twin of ``PendingBlog``). Purely client-side: on send
 * the chip becomes an explicit «استخدم القالب: «العنوان»» line appended to the
 * outgoing message — the writer_planner matches that title in its
 * ``<my_templates>`` block and sets ``chosen_template``, so no id is ever
 * sent over the wire. At most ONE template chip at a time (the planner drafts
 * from a single template); picking another replaces it.
 */
export interface PendingTemplate {
  templateId: string;
  title: string;
}

/**
 * Response from ``POST /api/v1/templates/ingest`` — the dedicated endpoint
 * that turns an attached workspace_item into a cleaned, placeholder'd
 * قوالبي template (writer_planner_user_templates plan, Wave E / D8).
 *
 * The backend returns HTTP 200 with this body for BOTH outcomes:
 *   success → ``{ ok: true, template_id, title }``
 *   failure → ``{ ok: false, error }`` (Arabic message)
 * so the caller branches on ``ok`` rather than catching an HTTP error.
 */
export type TemplateIngestResponse =
  | { ok: true; template_id: string; title: string }
  | { ok: false; error: string };

// ==========================================
// SSE EVENTS (Agent)
// ==========================================

export interface SSEAgentRunStarted {
  agent_family: AgentFamily;
  subtype?: string | null;
}

/**
 * deep_search_progress_bar plan — the four ordered stages of a deep_search
 * run, plus the terminal ``done`` marker that carries the run totals for the
 * collapsed summary chip.
 *
 * ``done`` is NOT a step: the tracker renders four steps
 * (planning → searching → aggregating → writing) and treats ``done`` as
 * "all four complete".
 */
export type DeepSearchStage =
  | "planning"
  | "searching"
  | "evaluating"
  | "aggregating"
  | "writing"
  | "done";

/**
 * Optional evidence payload on an ``agent_progress`` event. EVERY key is
 * optional — a phase-boundary event may carry counts while a lifecycle event
 * carries none, so consumers must treat a missing key as "unchanged", never
 * as zero.
 */
export interface SSEAgentProgressData {
  /** Cumulative retrieved/kept results so far. */
  sources?: number;
  /** Sub-queries the planner generated. */
  queries?: number;
  /** Planner-picked sectors. */
  sectors?: string[];
  /** Which executor phase just finished. */
  phase?: "reg_compliance" | "case";
  confidence?: number;
  elapsed_s?: number;
}

/**
 * ``event: agent_progress`` — the live staged-progress event emitted by the
 * deep_search branch of the orchestrator (planner-level only; the reg /
 * compliance / case executor loops stay batched). Drives
 * ``DeepSearchProgress``; the terminal ``stage: "done"`` event seals
 * ``chat-store.deepSearchSummaries[assistant_message_id]``.
 */
export interface SSEAgentProgress {
  stage: DeepSearchStage;
  /** Arabic detail line for the active stage. */
  text?: string | null;
  data?: SSEAgentProgressData | null;
}

/**
 * ``event: status`` — free-text Arabic progress line. The backend has emitted
 * these from ~50 call sites all along (batch-flushed just before the answer
 * tokens); the frontend now consumes them to populate the expanded log of the
 * deep_search summary chip. Ignored when no deep_search run is in flight.
 */
export interface SSEStatus {
  text: string;
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

/**
 * writer_planner_user_templates plan, Wave E (D6):
 *
 * Emitted by the writer pipeline at the END of a writing turn (after the
 * draft is published) when the planner judged an attached document to be
 * template-worthy and the user didn't already ask to save it. Non-blocking
 * — no pause. The frontend surfaces an inline "احفظ المرفق كقالب؟" chip on
 * the assistant bubble; clicking it POSTs ``item_id`` to
 * ``/templates/ingest`` which runs the template_ingester agent directly
 * (no router/planner) and inserts a قوالبي row.
 */
export interface SSETemplateSaveOffer {
  type: "template_save_offer";
  /** The attached workspace_item to ingest as a template. */
  item_id: string;
  /** The attached document's title, used as the chip's context hint. */
  title_hint: string;
}

// ==========================================
// SEND MESSAGE PAYLOAD
// ==========================================

export interface SendMessagePayload {
  content: string;
  attachment_ids?: string[] | null;
}

// ==========================================
// ON-DEMAND REFERENCE SOURCE REVEAL (access-tiers Phase C, §6.2)
// ==========================================
// GET /api/v1/workspace/{item_id}/references/{n}/source
//   authed · `Cache-Control: private, no-store` · 20/min per verified caller
//   (one shared budget with `/library/full/*`, so a 429 is reachable).
//
//   200 → { n, ref_id, domain, source_view, unlocked, balance }
//   402 → the D14 refusal body (parsed by `parseRefusal`, rendered by
//         `refusalCardCopy` — both in lib/library/*). NO content bytes.
//   404 → «العنصر غير موجود» / «المرجع غير موجود» / «تعذّر عرض هذا المصدر».
//         The last one is a corpus gap, not a refusal — the unlock (if one was
//         spent) is permanent, so a later retry costs nothing.

/**
 * Why the reveal succeeded. Only `granted` actually spent anything:
 * - `granted`          — a ledger row was just written; `cost` was charged.
 * - `already_unlocked` — the item was on the shelf already (free, forever).
 * - `open`             — policy-open item (compliance service, short تعميم);
 *                        the quota was never consulted, so `balance` is null.
 */
export type ReferenceUnlockReason = 'granted' | 'already_unlocked' | 'open';

/**
 * WHAT was unlocked — not what was clicked (D15.1).
 *
 * ~81% of `reg:` citations resolve to the whole نظام rather than the single
 * chunk the lawyer clicked, because only 2,140 of 11,455 chunks own exactly one
 * مادة. The unlock genuinely covers the entire statute (and every مادة under
 * it, per D5), so the UI must say so: a reader who thinks they spent an unlock
 * on one paragraph has been tricked by the interface, not by the meter.
 */
export interface ReferenceUnlockInfo {
  /** `regulation` | `article` | `judgment` | `circular` | `service` | … */
  content_type: string;
  content_id: string;
  /** Human title of the unlocked item — the نظام, never the chunk. */
  title: string;
  /** Non-null only when the chunk owned exactly one مادة. */
  article_no: number | null;
  charged: boolean;
  /** Weighted cost (§1.2.1): 1 for most items, up to 8 for a large نظام. */
  cost: number;
  reason: ReferenceUnlockReason;
}

/**
 * The «فتح المصادر» allowance AFTER this reveal (a `granted` decision reports
 * the post-charge number). `null` on the response — not here — means the quota
 * was never consulted; `limit: null` means unlimited. Those are different, and
 * conflating them shows «٠ متبقٍ» to a dev account.
 */
export interface ReferenceBalance {
  used: number;
  /** `null` = unlimited. */
  limit: number | null;
  resets_at: string | null;
}

/** 200 body of the metered reveal. */
export interface ReferenceSourceResponse {
  n: number;
  ref_id: string;
  /** `ReferenceDomain` on every real row; typed loosely — it is display-inert. */
  domain: string;
  source_view: SourceView;
  unlocked: ReferenceUnlockInfo;
  /** `null` for a policy-open item — the meter was never consulted. */
  balance: ReferenceBalance | null;
}

/** Why a reveal produced no source, when it was NOT an entitlement refusal. */
export type ReferenceSourceError =
  /** No in-memory access token (session never hydrated). */
  | 'no_token'
  /** 401/403 — the session died. Card + login CTA, never a forced redirect. */
  | 'unauthorized'
  /** 404 — unknown item/ref, or the corpus row is gone. */
  | 'not_found'
  /** 429 — the shared 20/min library budget. NOT a quota refusal. */
  | 'rate_limited'
  /** Network failure / unparsable body. */
  | 'network'
  /** 5xx or any other unexpected status. */
  | 'server';
