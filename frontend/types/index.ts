// ==========================================
// USER & AUTH
// ==========================================

export interface User {
  user_id: string;
  email: string;
  full_name_ar?: string | null;
  /** «بماذا تحب أن نناديك؟» (users.preferred_name, migration 122). null = never
   *  answered → إعدادات الحساب prefills with `call_name` instead. */
  preferred_name?: string | null;
  /** What the app actually calls the user: preferred_name, else a first name
   *  derived server-side from full_name_ar, else null when we have no name.
   *  Resolved in one place (shared/identity.py) — never re-derived here. */
  call_name?: string | null;
  /** Legacy column — superseded by plan_id. */
  subscription_tier?: string | null;
  /** Subscription plan (plans table). null = account not activated yet.
   *
   *  ⚠ NOT a paid-ness test. `plan_id !== "free"` is true for dev grants, comps
   *  and long-expired terms alike — use `paid_activated_at` for "this account
   *  actually bought something". */
  plan_id?: string | null;
  /** ISO timestamp of the last time this account activated a paid plan BY
   *  PAYING for it — null for free, dev, comped and expired subscriptions.
   *  Server-resolved from source + plan + a still-running term
   *  (`subscription_service.resolve_paid_activated_at`); absent on the /login
   *  payload, which does not read the subscription at all. Gates the
   *  post-purchase «اتعرف على ريحان» tour. */
  paid_activated_at?: string | null;
  created_at?: string | null;
  /** Account deletion grace period (30 days). True → app is gated behind
   *  AccountDeletionPendingScreen until restored. */
  deletion_pending?: boolean;
  deletion_requested_at?: string | null;
  /** Server-computed purge date (requested + 30 days) — never derived client-side. */
  purge_at?: string | null;
  /** True when the account holds a password credential (migration 141).
   *  Decides «تعيين» vs «تغيير كلمة المرور» in إعدادات الحساب and which branch
   *  DeleteAccountDialog confirms with.
   *
   *  ⚠ Do NOT re-derive this from the Supabase session's `app_metadata.providers`
   *  — that is what this field replaced. Setting a password on a Google account
   *  writes the credential without adding an `email` identity, so the session
   *  still reads ["google"] forever afterwards and the settings dialog would go
   *  on hiding the password form from someone who has one. Server-resolved from
   *  auth.users.encrypted_password; absent/undefined is treated as false. */
  has_password?: boolean;
  /** Onboarding profession segment (users.profession_group, migration 115).
   *  EXACTLY null = never asked → the profession prompt opens. "unknown" is
   *  the server's fail-closed sentinel for degraded reads; "declined" = chose
   *  not to answer; else legal | entrepreneur | specialist | individual. */
  profession_group?: string | null;
  /** Finer segment (chip pick or free-typed «أخرى») — specialist/individual only. */
  profession_label?: string | null;
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
  /**
   * DERIVED, server-side: this row is the ONE shared «محادثة تجريبية» every
   * account sees (demo_conversation_product_tour §3.2). The id itself never
   * reaches the client — components branch on this flag alone, so the fixture
   * can be repointed backend-side without a frontend release.
   *
   * Read-only for everyone (D2): the composer becomes a hint bar, the WI's
   * 👍/👎 are hidden (`workspace_items.feedback` is ONE shared column),
   * مشاركة / حفظ كمدونة / «+» render disabled, and «حذف» becomes «إخفاء» —
   * a per-user preference flag, never a shared soft-delete (D8).
   *
   * Absent on every ordinary conversation, hence optional.
   */
  is_demo?: boolean;
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

/**
 * The library page types that can be CARRIED into a conversation
 * (`.claude/plans/simple_search_family.md` §8 / §12a C3).
 *
 * A strict subset of `LibraryPageType` (`@/types/library`): `fetch_grounding`
 * has no grounder for `circular` / `form` / `calculator` / `topic`, so the
 * backend answers those with an Arabic error. The UI must therefore never
 * offer the carry button on them — see `isCarryablePageType` in
 * `components/library/blocks/AskRayhanWidget.tsx`, whose type predicate is
 * what pins this subset relation at compile time.
 */
export type LibraryItemPageType =
  | "regulation"
  | "article"
  | "judgment"
  | "blog";

/**
 * A library page the user asked to bring into the chat, before a conversation
 * exists to hold it — the `pendingBlogTokens` twin (§8 "New-chat carry").
 * `pageId` is the public slug; for an `article` it is the composite
 * `{reg_slug}/{article_slug}` shape `fetch_grounding` already parses.
 */
export interface LibraryItemRef {
  pageType: LibraryItemPageType;
  pageId: string;
  /** Page heading, known client-side — shown on the chip before the POST lands. */
  title: string | null;
}

/**
 * A library object attached to the composer, rendered as a chip beside file
 * attachments — the `PendingBlog` twin for «تحدّث مع ريحان عن هذه الصفحة»
 * (`.claude/plans/simple_search_family.md` §8).
 *
 * Created at attach time via `POST /conversations/{id}/library-items` as a
 * `kind='references'` workspace item (uncapped — it never crowds the 15-item
 * workspace); on send the `itemId` joins the existing `attachment_ids` array,
 * so nothing about the send payload changes.
 */
export interface PendingLibraryItem extends LibraryItemRef {
  /** Chip id (client-generated). */
  id: string;
  /** ``loading`` = the POST is in flight; ``failed`` chips never block send. */
  status: "loading" | "ready" | "failed";
  /** workspace_items.item_id once the POST returns; null before that. */
  itemId: string | null;
  /**
   * True only when the server explicitly reported that THIS attach created the
   * item (`already_attached === false`). Removing the chip deletes the item
   * only then — an unknown/absent flag stays `false` so we can never delete a
   * card the user had already put in the conversation.
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
  /**
   * The plan the block was enforced against — EFFECTIVE, so an expired paid
   * subscription that fell back reports `"free"`. `null` = no plan assigned
   * (account not activated), which buying a plan does not fix. Optional so a
   * pre-deploy backend that omits the field degrades to the banner rather than
   * mis-triggering.
   *
   * Drives the AUTO-OPENING modal only, which stays free-only — WHAT to offer
   * comes from `upgrade_options`. A full-screen pitch at someone who already
   * paid reads differently than one at a free user.
   */
  plan_id?: string | null;
  /**
   * The plans that would actually unblock this send: purchasable, priced above
   * the user's plan, AND with a strictly higher limit on the window that
   * blocked them — ordered by price, cheapest first.
   *
   * Computed server-side (`shared/quota._upgrade_options`) because the enforced
   * limits live in the `plans` table and `lib/pricing.ts` carries them only as
   * Arabic prose; a client-side ladder would need a second copy of every number
   * to drift against.
   *
   * Empty = there is nothing to sell: `max` is already at the top of the ladder,
   * and an unactivated account (`plan_id: null`) is not fixed by a purchase.
   * Optional so a pre-deploy backend that omits the field degrades to
   * banner-only (a missed upsell, never a pitch that cannot help).
   */
  upgrade_options?: string[];
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

/**
 * The cited object's WING — the read path keys every shell builder off this.
 *
 * `regulations` means **a chunk** of a نظام (`chunks_v2.id`), which is why the
 * two `simple_search` additions could not reuse it:
 *
 * - `articles`         — one مادة (`articles_v2.id`), `ref_id` = `article:<uuid>`
 * - `regulation_docs`  — a WHOLE نظام (`regulations_v2.id`), `ref_id` =
 *                        `regdoc:<uuid>`
 *
 * ⚠ NEVER give either of them the `reg:` prefix. `domain='regulations'` hard-
 * assumes the id is a `chunks_v2.id`; a regulation/article uuid passes the uuid
 * check, inserts cleanly, then resolves to nothing on read and renders a dead
 * stub with no «عرض المصدر» — zero errors anywhere in the chain.
 *
 * Mirrors the DB CHECK on `workspace_item_references.domain` and the Literal on
 * `agents/deep_search_v4/aggregator/models.py::Reference.domain`. The
 * `Record<ReferenceDomain, …>` tables in `ReferencePanel` are exhaustive on
 * purpose: adding a member here must not compile until the panel knows it.
 */
export type ReferenceDomain =
  | 'regulations'
  | 'compliance'
  | 'cases'
  | 'circulars'
  | 'articles'
  | 'regulation_docs';

export type ReferenceSourceType =
  | 'article'
  | 'section'
  | 'chunk'
  | 'regulation'
  | 'gov_service'
  | 'form'
  | 'case'
  | 'circular'
  // simple_search (§6.1). Deliberately NOT `article` / `regulation` — those two
  // names are already taken by the legacy views below, and reusing them gives
  // the backend's Pydantic discriminated union duplicate discriminator values
  // (an import-time crash) and the frontend union a silent wrong-arm match.
  | 'article_full'
  | 'regulation_summary';

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
      /**
       * Parent regulation's landing page — the ONE external exit. The PDF
       * companion was removed with the reference redesign (2026-08-01): the
       * source popup offers exactly two ways out, the official link and the
       * item's page in our own library.
       */
      regulation_source_url: string;
    }
  | {
      source_type: 'case';
      title: string;
      /**
       * The ruling's structured ملخص (`cases.summary`, falling back to
       * `short_summary`) — markdown, and what the popup RENDERS. The raw
       * judgment text is deliberately not here: «فتح الحكم في ريحان» opens
       * `/judgments/{slug}`, and the reveal already paid the unlock for that
       * same document, so the full text costs nothing extra to reach.
       */
      summary: string;
      /**
       * Raw ruling text (markdown) — a LAST-RESORT body, non-empty only for the
       * handful of rulings that carry no summary. Never both this and `summary`.
       */
      content: string;
      /**
       * The ruling's own page on the issuing body's site — وزارة العدل only (20,671
       * of 30,531 rulings). The rest were parsed out of PDFs and reach their source
       * through `official_sources`. This stays the target of «فتح المصدر الرسمي».
       */
      details_url: string;
      /**
       * «المجلد» / «الصفحات» — which bound مجلد this ruling was lifted out of and at
       * which pages. Present for the 5,538 volume-parsed rulings, empty for a وزارة
       * العدل ruling (it has its own page) and for a standalone قرار PDF (one file,
       * one decision — a page range would describe the file, not locate anything).
       *
       * OPTIONAL: artifacts persisted before 2026-08-19 have no such key, and their
       * dialogs simply render no citation block.
       */
      citation?: { label: string; value: string }[];
      /**
       * The publisher's collection page and the volume/decision PDF, for the 9,860
       * rulings with no `details_url` of their own. This is what lets «عرض المصدر»
       * answer "where is this from?" for a قرار زكوي or a ديوان المظالم ruling —
       * before it, those revealed a body with no attribution at all.
       *
       * Safe to render here even though the /judgments page withholds the same links
       * from anonymous readers: reaching this view already spent the item's unlock.
       */
      official_sources?: { title: string; href: string }[];
    }
  | {
      /**
       * A government service — title and link, NO body, and that is still true
       * (2026-08-19). The popup used to restate the service's intro, الخطوات,
       * المتطلبات and المستندات المطلوبة. That content is gone and stays gone: a
       * procedure goes stale when its issuing entity edits it, and repeating the
       * entity's own text under our chrome makes us look like the authority on a
       * process we don't own. The service's own page is the authority.
       *
       * What DID come back is the `/compliance` wing, with different content:
       * **service guides**, our own authored rewrite of the issuing entity's
       * official PDF user guide, published in full and ungated at
       * `/compliance/{slug}`. ~169 of 4,746 services have one. That changes the
       * dialog's EXITS, not this view — when a guide exists the panel gains a
       * second button («افتح الدليل الشامل للخدمة في ريحان») and the reader
       * leaves for the guide's own page. No guide content is ever inlined here,
       * and none of it enters agent context.
       */
      source_type: 'gov_service';
      title: string;
      /**
       * `services.service_url` — the OFFICIAL exit, and still the only outbound
       * one: the guide never links the source PDF, and the المنصة الوطنية portal
       * link (`national_platform_url`) went with the body, because a portal home
       * page is not the cited source. May be "" when the corpus has no link, in
       * which case the popup shows the title alone (plus the guide exit, if the
       * backend resolved one).
       */
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
  // ---- simple_search (§6.1a) ----------------------------------------------
  // ⚠ These MUST stay ABOVE the legacy arm. That arm is a permissive
  // ``[k: string]: unknown`` bag, so a variant whose ``source_type`` overlapped
  // it would be absorbed with NO compile error and would render through
  // ``SourceViewContent``'s fall-through as bare markdown — looking like it
  // works. The distinct ``article_full`` / ``regulation_summary`` discriminators
  // are what keep the narrowing honest; never rename them to
  // ``article`` / ``regulation``.
  | {
      /**
       * ONE مادة, served whole — ``articles_v2.content`` verbatim, never a chunk
       * slice. This is simple_search's L3 leg, and the one behavioural
       * difference from deep_search, where a fetched article is text-only and
       * never becomes a citation.
       */
      source_type: 'article_full';
      /** «المادة 81 من نظام العمل». */
      title: string;
      /**
       * ``articles_v2.article_number`` — **text**, not a number: the corpus
       * carries forms like «81 مكرر» that no integer column can hold. Rendered
       * verbatim, in Western digits (§6.4's digit rule).
       */
      article_num: string | null;
      /**
       * The FULL article body (markdown). Named ``content`` on purpose:
       * ``extractSourceContent`` does ``"content" in view ? view.content : ""``,
       * so any other field name silently yields a blank dialog with no copy
       * button.
       */
      content: string;
      /** Parent نظام's title, for the dialog's «نص المادة … — …» header line. */
      regulation_title: string;
      /** Parent ``regulations_v2.landing_url`` — the ONE external exit. */
      regulation_source_url: string;
    }
  | {
      /**
       * A WHOLE نظام, represented by its SUMMARY. The body here is
       * ``regulations_v2.llm_summary`` (falling back to ``summary``) —
       * deliberately NOT the statute's full text, which runs past 1.1M chars for
       * outliers and belongs on the library page this dialog links to. The popup
       * frames it as a ملخص so it is never mistaken for the نظام itself.
       */
      source_type: 'regulation_summary';
      /** ``clean_title`` or ``title``. */
      title: string;
      /** The regulation's summary (markdown). See ``article_full.content``. */
      content: string;
      /** ``regulations_v2.landing_url`` — the ONE external exit. */
      regulation_source_url: string;
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
   * panel then falls back to the domain's own label. «غير محدد» is the
   * corpus's "could not determine" sentinel and is treated as absent.
   *
   * Carried by the two regulation-backed domains only: `regulations` (a chunk
   * of the document) and `regulation_docs` (the whole document). NOT by
   * `articles` — a مادة is «المادة» whatever its parent's doc type is.
   */
  doc_type?: string;
  /**
   * «الإحالات» — the citations this source points at, rendered inside the
   * source-reveal dialog under the body.
   *
   * Populated for BOTH regulations (projected from `cross_references_v2`) and
   * cases (a ruling's `referenced_regulations`, normalised onto this same shape
   * by `preprocessor.case_ref_to_cross_ref`) — one shape, one renderer. Empty
   * for compliance / circulars, which have no citation mesh.
   */
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
   * The cited item's page in OUR library («فتح الحكم في ريحان»). `null` when
   * the item has no published page — the card then renders the external link
   * alone, never a hub fallback. Absent on pre-existing blog snapshots.
   *
   * NAVIGATION, NOT CONTENT: this link is free and unmetered. The library page
   * enforces its own access tier; metering the link too would double-charge.
   * Compliance references never carry one — `/compliance` was retired and has
   * no wing to point at.
   */
  library_url?: string | null;
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
 * Response of ``POST /api/v1/conversations/{id}/library-items`` — the pinned
 * Case-B route (`.claude/plans/simple_search_family.md` §12a C3).
 *
 * Body in: ``{ page_type, page_id }``. Body out: the created (or deduped)
 * ``kind='references'`` workspace item. The contract pins only ``item``;
 * ``already_attached`` is the blog twin's dedup flag and is treated as
 * OPTIONAL here so a backend that omits it still typechecks — its absence is
 * read as "unknown", which keeps chip removal from deleting anything.
 */
export interface LibraryItemResponse {
  item: WorkspaceItem;
  already_attached?: boolean;
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
  /**
   * «جولة المخرجات» — the 5-step coach-mark tour over the shared demo
   * conversation. Absent/false → the tour auto-starts once, AFTER «اتعرف على
   * ريحان» is dismissed; finishing or skipping PATCHes it to true.
   */
  tour_workspace_seen?: boolean;
  /**
   * D8 dismiss for the shared demo conversation: «إخفاء» hides the row from
   * THIS user's sidebar only. Never a soft-delete — the conversation is one
   * shared row, so a delete by one user would vanish it for everyone.
   */
  demo_conversation_hidden?: boolean;
  /**
   * «عندك رمز تفعيل؟» — the two-week activation-code popup has been resolved
   * (redeemed OR dismissed). Absent/false → it opens once, ahead of «اتعرف على
   * ريحان», while `promo-campaign.ts` says the window is still open.
   */
  promo_code_popup_seen?: boolean;
  /**
   * «سلسلة تعلّم ريحان» — lifetime count of user messages that completed a turn
   * (bumped on the `done` SSE event). Drives the every-4-messages lesson
   * cadence; see `stores/edu-store.ts` and `.claude/plans/edu_series.md`.
   */
  edu_turns?: number;
  /** ISO instant of the last delivered lesson — the one-per-day spacing anchor. */
  edu_last_shown_at?: string;
  /**
   * Per-lesson «seen» flags are written as `edu_<lesson_id>: true` and covered
   * by the index signature below rather than enumerated here — the syllabus is
   * data (`components/edu/edu-syllabus.tsx`), and adding a lesson must not
   * require a type change.
   *
   * ⚠ Every key in here is FLAT and stays flat. `merge_preferences` is a
   * SHALLOW merge server-side, so a nested object written by one tab clobbers
   * the sibling keys another tab wrote (see [[project_edu_popups]]). This is
   * exactly why the lesson flags are `edu_usage_limits: true` and not
   * `edu: { seen: {...} }`.
   */
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
  /**
   * Non-null only when the chunk owned exactly one مادة.
   *
   * ⚠ STRING, not number (§12a C4). `articles_v2.article_number` is TEXT and
   * carries «1-1» and «81 مكرر» — 487 of 51,792 rows. Typed `number` the
   * backend had to send `null` for exactly those, and the notice silently fell
   * back to naming the whole نظام *after* the reader spent an unlock. Pinned
   * `string | null` at every hop; render it with `arDigits`, never `arNumber`
   * (which rounds).
   */
  article_no: string | null;
  charged: boolean;
  /** Weighted cost (§1.2.1): 1 for most items, up to 8 for a large نظام. */
  cost: number;
  reason: ReferenceUnlockReason;
}

/**
 * The «فتح المصادر» allowance AFTER this reveal (a `granted` decision reports
 * the post-charge number). `null` on the response — not here — means the quota
 * was never consulted; `limit: null` means unlimited. Those are different, and
 * conflating them shows «0 متبقٍ» to a dev account.
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
  /**
   * The cited item's page in OUR library — `/regulations/{slug}`,
   * `/judgments/{slug}`, `/circulars/{slug}` or `/compliance/{slug}` — for the
   * «فتح ... في ريحان» button beside the official link.
   *
   * `null` means the item has NO published page (no `seo_item_meta` slug yet),
   * and the button must then be dropped entirely. Never substitute the wing hub:
   * a button that promises the document and delivers a list is worse than one
   * that isn't there.
   *
   * A مادة-level citation resolves to its **نظام** page, not to
   * `/regulations/{reg}/{article}` — the chunk behind a citation is an arbitrary
   * slice and 81% of them already lift to the whole statute, so the button
   * always lands in the same place.
   */
  library_url: string | null;
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

// -----------------------------------------------
// Payments — Moyasar one-time checkout (Wave 1)
// (.claude/plans/moyasar_payments.md Phases C + D)
// -----------------------------------------------
//
// ⚠ NO AMOUNT EVER TRAVELS UP FROM THE CLIENT. The browser sends a `plan_id` and
// receives an amount the server computed from `plans.price_sar` minus a
// server-computed upgrade credit. Nothing in these shapes is a price input, and
// nothing here should ever grow one.

/**
 * `payment_transactions.status`. `initiated` is the row before the browser form
 * has created anything at Moyasar; `pending` is what `/verify` answers when the
 * fetched payment is not yet terminal (the pre-3DS `on_completed` call).
 */
export type PaymentStatus =
  | 'initiated'
  | 'pending'
  | 'paid'
  | 'failed'
  | 'refunded';

/**
 * `POST /payments/checkout` → everything the embedded form needs.
 *
 * The publishable key is served HERE rather than as a `NEXT_PUBLIC_*` build
 * arg: it sidesteps the Docker build-arg trap (`project_domain_rayhanai`) and
 * lets test↔live switch with an env change and no frontend rebuild.
 */
export interface PaymentCheckoutResponse {
  /** Our `payment_transactions.payment_id` — travels as `metadata.payment_id`. */
  payment_id: string;
  /** ⚠ HALALAS. Hand to the form verbatim; divide by 100 only for display. */
  amount_halalas: number;
  /**
   * Prorated credit for the remaining value of an active PAID plan being
   * upgraded away from, already subtracted from `amount_halalas`. `"0.00"` for
   * a fresh purchase, a same-plan stack, or a code/marketing-sourced
   * subscription (promo grants must never convert into cash discounts).
   *
   * ⚠ SAR amounts arrive as 2-dp STRINGS ("77.91") — JSON numbers drop
   * trailing zeros and floats are the wrong shape for money. `formatSar`
   * accepts them directly; convert with `Number()` before any arithmetic.
   */
  credit_sar: string;
  /** Arabic description shown inside the form and on the Moyasar receipt. */
  description: string;
  /** `pk_test_…` / `pk_live_…`. */
  publishable_key: string;
  /** Absolute URL Moyasar redirects the browser back to (`?id=<uuid>`). */
  callback_url: string;
  /**
   * Server kill-switch (`MOYASAR_APPLEPAY_ENABLED`) — the form offers Apple
   * Pay only when this is true AND the browser passes the ApplePaySession
   * capability gate. Off while the Moyasar domain registration is pending.
   */
  applepay_enabled: boolean;
  /**
   * The recurring-billing disclosure to show BEFORE the card form, **rendered
   * verbatim**. Null whenever no consent is required (basic, or the renewal
   * feature flag off).
   *
   * ⚠ NEVER BUILD THIS SENTENCE CLIENT-SIDE and never reword it. The server
   * hashes its own copy of this exact string into `consent_text_hash` — that
   * hash IS the consent artefact, and a client that paraphrases (or localises,
   * or re-orders the amount and the date) makes it prove nothing. If the flag
   * is on and this is empty, the page degrades to a plain one-time purchase
   * rather than inventing a disclosure.
   */
  recurring_disclosure_ar: string | null;
  /**
   * Server verdict: this checkout may not proceed until the user has ticked
   * the disclosure above and the tick has been recorded via
   * `POST /payments/{payment_id}/consent`.
   *
   * The single gate for the whole feature — true only for `pro`/`max` with the
   * backend renewal flag on. A backend that predates the feature omits the
   * field entirely, which reads as `undefined` → falsy → today's behaviour
   * exactly. Card tokenization (`credit_card.save_card`) is gated on this and
   * nothing else.
   */
  requires_recurring_consent: boolean;
}

/**
 * `POST /payments/verify` → the SYNC result, not a paid-assert.
 *
 * Called twice per purchase from two different moments (pre-3DS `on_completed`,
 * then the callback page), so `pending` is an ordinary answer and not an error:
 * it means "the id is now recorded, nothing granted yet".
 *
 * Only `status` is guaranteed. The rest is present when the server has it —
 * typed optional on purpose so a backend that grows fields later cannot break
 * this client, and so the callback page degrades to a generic success message
 * rather than rendering «undefined».
 */
export interface PaymentVerifyResponse {
  status: PaymentStatus;
  /**
   * Whether the term was actually applied. `status:"paid"` with
   * `granted:false` = money in, grant pending — render «جارٍ التفعيل» and keep
   * polling, NEVER a success screen (the webhook completes the grant).
   */
  granted?: boolean;
  payment_id?: string | null;
  /** Which plan was (or would be) granted — drives the retry CTA on failure. */
  plan_id?: string | null;
  plan_name_ar?: string | null;
  /** ISO timestamp the granted term ends. */
  expires_at?: string | null;
  /** Arabic failure reason from the provider, when the status is `failed`. */
  message?: string | null;
}

/** One row of `GET /payments/history` — the سجل المدفوعات receipts list. */
export interface PaymentHistoryItem {
  payment_id: string;
  plan_id: string;
  plan_name_ar?: string | null;
  /** What was actually CHARGED (catalog price minus any upgrade credit).
   *  2-dp string — see the note on `PaymentCheckoutResponse.credit_sar`. */
  amount_sar: string;
  /** VAT stamped at purchase — stored, never recomputed at display time. */
  vat_amount_sar?: string | null;
  /** Prorated upgrade credit deducted at checkout; `"0.00"`/null on a plain buy. */
  upgrade_credit_sar?: string | null;
  status: PaymentStatus;
  created_at: string;
  paid_at?: string | null;
  refunded_at?: string | null;
  /** What was returned after the processing fee — stamped on refund. */
  refunded_amount_sar?: string | null;
  /** The fee actually retained, as charged at the time (a later fee change
   *  must not rewrite this row). */
  refund_fee_sar?: string | null;
  /**
   * Server's own verdict on the 24-hour window. When present it WINS over the
   * client's `paid_at` arithmetic — clocks disagree, and the server is the one
   * that will enforce it. When absent the UI falls back to computing it.
   */
  refundable?: boolean;
  /** ISO timestamp the refund window closes (server-computed, informational). */
  refund_deadline?: string | null;
  /**
   * Server-quoted refund arithmetic, present only while `refundable`.
   * The deduction is NOT a flat constant — it recovers the provider fee
   * Moyasar charged for this specific payment plus their flat refund-execution
   * fee plus our margin, so only the server can compute it. 2-dp strings.
   */
  refund_quote_fee_sar?: string | null;
  refund_quote_amount_sar?: string | null;
}

export interface PaymentHistoryResponse {
  payments: PaymentHistoryItem[];
}

/** `POST /payments/{id}/refund` → the executed partial refund. */
export interface PaymentRefundResponse {
  payment_id: string;
  status: PaymentStatus;
  /** Amount actually returned to the card (charge − processing fee). 2-dp string. */
  refunded_amount_sar: string;
  refund_fee_sar: string;
  /** Whether the granted term was undone alongside the money. */
  revoked?: boolean;
  /** Which `revoke_plan_grant` branch ran (`restored`, `subtracted`, …) — logged, not shown. */
  revoke_action?: string | null;
}

// -----------------------------------------------
// Recurring consent + the stored card (التجديد التلقائي)
// (.claude/plans/subscription_auto_renewal.md §6 + §9)
// -----------------------------------------------
//
// ⚠ NO CARD DATA EVER TRAVELS THROUGH THESE SHAPES. The provider token lives
// server-side in `payment_methods` behind an RLS lockdown and is never
// serialised to a browser; what comes back here is a display mask (brand +
// last4 + expiry) plus the timestamp of the consent. Nothing in this block
// should ever grow a token, a PAN or a CVV field.

/**
 * `POST /payments/{payment_id}/consent` with `{accepted: true}`.
 *
 * The caller only needs the 2xx — the artefact (`consent_given_at` +
 * `consent_text_hash`) is written server-side against the text the server
 * itself chose. Every field is optional so a `204 No Content` (which
 * `apiFetch` hands back as `{}`) is a perfectly valid answer.
 */
export interface PaymentConsentResponse {
  accepted?: boolean;
  /** ISO timestamp the consent was recorded. */
  consent_given_at?: string | null;
}

/**
 * `GET /payments/method` — the stored-credential surface for إعدادات الحساب.
 *
 * `has_method` is the ONLY field guaranteed to be meaningful: everything else
 * is null when nothing is stored, and the display fields are whatever the
 * provider returned at tokenization (never anything the user typed here).
 * A backend without this endpoint 404s, which the hook treats as "no method"
 * — so the section simply does not render.
 */
export interface PaymentMethodState {
  has_method: boolean;
  /** `mada` | `visa` | `mastercard` | … — provider wording, mapped for display. */
  brand: string | null;
  /** Last four digits, as the provider returned them. Rendered LTR. */
  last4: string | null;
  /** 1–12. */
  exp_month: number | null;
  /** Four-digit year (a two-digit year is normalised at display time). */
  exp_year: number | null;
  /** When the recurring disclosure was accepted. A method with no consent is
   *  not chargeable — the renewal job treats it as absent. */
  consent_given_at: string | null;
}

/**
 * `DELETE /payments/method` → revocation.
 *
 * Typed permissive because the useful answer is the status code: the server may
 * reply with the emptied state or with `204`. The client never reads the body —
 * it writes the known-empty state into the cache and re-reads.
 */
export type PaymentMethodRevokeResponse = Partial<PaymentMethodState>;

// -----------------------------------------------
// المشتركون الأوائل — early-adopters campaign
// (.claude/plans/early_adopters.md)
// -----------------------------------------------

/**
 * `GET /payments/early-adopter` — PUBLIC, unauthenticated, server-side cached.
 *
 * ⚠ THERE IS NO COUNT IN THIS SHAPE, AND NONE MAY BE ADDED. Not a remaining
 * seat count, not a seat total, not a closing date (owner decision, plan §1.10):
 * the API does not carry them and the UI must not render them even if it one day
 * does. The single permitted scarcity signal is the literal «المقاعد محدودة»
 * (`SEATS_LIMITED_NOTE` in `lib/pricing.ts`).
 *
 * `open` is the whole campaign flag — it answers "are seats still being
 * issued?", which is also what decides whether `basic` is discounted for
 * everyone. It is NOT "does the caller hold a seat"; that is `EarlyAdopterState`
 * on the authed subscription read, and the two are independent (a seat holder
 * keeps their price for 90 days after the campaign closes).
 *
 * `promo` maps `plans.plan_id` → the promotional amount as a 2-dp LATIN string
 * ("49.90"), exactly like every other money field on the wire. Render it through
 * `formatSar`, never by hand. A plan missing from the map has no promo; an
 * `open: false` answer carries `{}`.
 */
export interface EarlyAdopterCampaign {
  open: boolean;
  promo: Record<string, string>;
}

/**
 * The `early_adopter` block on `GET /payments/subscription` — the CALLER's own
 * standing, which is a different question from whether the campaign is open.
 *
 * Optional on `SubscriptionState` because of deploy skew: a backend from before
 * this shipped sends no block at all, and "absent" must read as "not a member"
 * rather than as an error. Never derive membership from the price on screen.
 *
 * `promo_ends_at` is the end of the 90-day window anchored at the claiming
 * payment's `paid_at` (wall-clock, so a gap in the subscription burns days
 * rather than pausing them). It is the user's OWN date, not a campaign closing
 * date — showing it discloses nothing about remaining capacity.
 */
export interface EarlyAdopterState {
  is_member: boolean;
  promo_ends_at: string | null;
}

// -----------------------------------------------
// Subscription cancellation (إلغاء الاشتراك)
// (.claude/plans/subscription_cancellation.md)
// -----------------------------------------------

/** The exit-survey answers. Mirrors the CHECK constraint in migration 120. */
export type CancelSubscriptionReason =
  | 'expensive'
  | 'no_longer_needed'
  | 'something_wrong'
  | 'other';

/**
 * `GET /payments/subscription` — and the answer to both cancel and reactivate,
 * so the settings dialog re-renders straight from the mutation response.
 *
 * ⚠ Cancelling stops NO automatic charge TODAY: pro/max are meant to auto-renew
 * (owner 2026-08-10, /terms §5.2) but the engine does not exist yet, so every
 * sale is still effectively a one-time term. This records the intent
 * (`renewal_cancelled_at`, which the Wave 2 renewal job must honour) and leaves
 * the current term completely alone. Copy built on this shape must therefore say
 * «لن يُجدَّد» — a forward-looking statement true in both worlds — and never
 * «سيتم إيقاف الدفع التلقائي», which asserts a live charge that does not exist.
 * See .claude/plans/subscription_auto_renewal.md.
 */
export interface SubscriptionState {
  /** null when the user has no subscription row at all. */
  plan_id: string | null;
  plan_name_ar: string | null;
  /** End of the paid term. null for a non-expiring grant. */
  expires_at: string | null;
  /** `payment` | `code` | `manual` | `signup` — only `payment` is cancellable. */
  source: string | null;
  /**
   * A PAID plan whose term is still running. Describes the SUBSCRIPTION, not
   * the button: it stays `true` while `renewal_cancelled_at` is set, because an
   * undo makes cancelling legal again. Branch on both.
   */
  cancellable: boolean;
  /** Set = the user has opted out of renewal; null = renewal is on. */
  renewal_cancelled_at: string | null;
  /**
   * المشتركون الأوائل standing. ABSENT on an older backend — treat a missing
   * block as "not a member" (see `EarlyAdopterState`). The cancel flow reads
   * this to warn a seat holder that cancelling forfeits the price permanently,
   * which is the one place that rule is stated before the user acts.
   */
  early_adopter?: EarlyAdopterState | null;
}
