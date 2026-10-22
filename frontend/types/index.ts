// ==========================================
// USER & AUTH
// ==========================================

// User, AuthTokens and AuthResponse live in the shared contracts folder
// (`lib/contracts/auth.ts`, also used by the mobile app).
export type { User, AuthTokens, AuthResponse } from "@/lib/contracts";

// Conversations, messages, agents/workspace, references and SSE payloads also
// live in the shared contracts folder (mobile Phase 2); each section below
// re-exports its names in place. These are the ones this file uses itself.
import type {
  ConversationSummary,
  Reference,
  TemplateScope,
  WorkspaceCreator,
  WorkspaceFeedback,
  WorkspaceItem,
} from "@/lib/contracts";

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

export type {
  ConversationSummary,
  ConversationDetail,
  ConversationListResponse,
  CreateConversationRequest,
} from "@/lib/contracts";

// ==========================================
// MESSAGES
// ==========================================

export type {
  MessageMetadataKind,
  MessageMetadata,
  TemplateScope,
  TemplateUsed,
  NextStepKind,
  NextStep,
  Message,
  Attachment,
  MessageListResponse,
} from "@/lib/contracts";

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
 * `components/library/blocks/ChatWithPageCta.tsx`, whose type predicate is
 * what pins this subset relation at compile time.
 *
 * Kept byte-for-byte in step with `library_item_service.SUPPORTED_PAGE_TYPES`.
 * `compliance` (a /compliance «الدليل الشامل») joined on 2026-09-07.
 */
export type LibraryItemPageType =
  | "regulation"
  | "article"
  | "judgment"
  | "blog"
  | "compliance";

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

export type {
  UploadInitResponse,
} from "@/lib/contracts";

// ==========================================
// SSE EVENTS
// ==========================================

export type {
  SSEMessageStart,
  SSEToken,
  SSEDuplicate,
  SSEQuotaExceeded,
  SSEParallelLimit,
} from "@/lib/contracts";

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
  /**
   * How many conversations may answer at the same time on the effective plan
   * (parallel_conversations plan; 5 for max/dev, 1 otherwise). Optional so a
   * backend that predates the field still type-checks — read it through
   * `maxParallelRuns()` (hooks/use-usage.ts), which defaults to 1.
   */
  max_parallel_runs?: number;
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

export type {
  SSEDone,
} from "@/lib/contracts";

// ==========================================
// AGENTS & WORKSPACE ITEMS
// ==========================================

export type {
  AgentFamily,
  TaskType,
  WorkspaceItemKind,
  WorkspaceCreator,
  WorkspaceFeedback,
  WorkspaceItemSubtype,
  WorkspaceItem,
  WorkspaceItemListResponse,
} from "@/lib/contracts";

// ==========================================
// DEEP_SEARCH ARTIFACT REFERENCES (JSON render object)
// ==========================================

export type {
  ReferenceDomain,
  ReferenceSourceType,
  CrossRef,
  SourceView,
  Reference,
  WriterMetadataReferenceView,
} from "@/lib/contracts";

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

// ⚠ `BlogCardPublic` / `PublicBlogsResponse` LIVED HERE AND ARE GONE
// (blog_subjects.md §2, 2026-09-02). They described `GET /public/blogs` while it
// listed `blog_posts` tokens; that endpoint now serves the versioned
// `public_blogs` wing and returns `{ blogs: PublicBlogCard[] }` — see the PUBLIC
// BLOG WING block near the end of this file. The `blog_posts` shapes above stay
// exactly as they are: they still back the 99 share links, مدوناتي and the
// import path. Nothing referenced the two removed types once `/blog` was
// rewritten, and leaving a type that describes a payload the backend no longer
// emits is how the next reader gets `undefined` with no error.

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
 *
 * ⚠ ``can_publish_public`` IS GONE (blog_subjects.md §8, D12). It mirrored
 * ``users.can_access_blog``, the curation gate on «نشر في المدونة العامة» —
 * a gate exactly one account held and never used. The public wing is its own
 * table now (``public_blogs``), written by the editorial service key, so the
 * flag guarded a door that no longer leads anywhere and the toggle it drove is
 * deleted. The column stays in the DB, dormant.
 */
export interface MyBlogsResponse {
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
// PUBLIC BLOG WING (public_blogs) — anon read surface
// ==========================================
// `.claude/plans/blog_subjects.md` §2/§3. A DIFFERENT TABLE from the
// `blog_posts` snapshot above: versioned, addressed by an Arabic `slug` with no
// token at all (D17 — a public blog is open by default, so the slug is the whole
// address), carrying a `type` badge and many `subjects`. The legacy shapes above
// stay exactly as they are; the 99 share tokens keep resolving on the same
// route.

/** One subject chip under a blog's byline — links to `/blog/{slug}`. */
export interface BlogSubjectRef {
  /** ASCII kebab-case, permanent. NEVER Arabic — see `lib/blog/slug.ts`. */
  slug: string;
  label_ar: string;
}

/**
 * One entry in the browse vocabulary (مواضيع) — `GET /public/blogs/subjects`.
 *
 * ``blog_count`` counts blogs whose CURRENT version is public + published + not
 * deleted. The endpoint returns the WHOLE active vocabulary with honest counts;
 * the `>= 1` filter that keeps empty subjects off the hub grid, out of
 * `/blog/subjects` and out of the sitemap (plan D13 + §7) is the CALLER's.
 */
export interface BlogSubject {
  slug: string;
  label_ar: string;
  description_ar: string | null;
  sort_rank: number;
  blog_count: number;
}

/**
 * One card in the public gallery or a subject feed —
 * `GET /public/blogs` · `GET /public/blogs/subjects/{slug}`.
 *
 * ``type`` is one of `laws_explanation` | `judicial_research` | `compliance`
 * (a DB CHECK, not an enum on the wire). Typed as a plain string on purpose:
 * it renders through `blogTypeLabel`, which falls back to the raw value, so a
 * fourth type added server-side degrades to a visible label instead of a
 * TypeScript lie. **Never a URL** (plan D3).
 */
export interface PublicBlogCard {
  slug: string;
  title: string;
  type: string;
  snippet: string;
  subjects: BlogSubjectRef[];
  view_count: number;
  created_at: string;
  updated_at: string | null;
}

/** `GET /api/v1/public/blogs` — the anonymous gallery feed, newest first. */
export interface PublicBlogListResponse {
  blogs: PublicBlogCard[];
}

/** `GET /api/v1/public/blogs/subjects` — the full browse vocabulary. */
export interface BlogSubjectsResponse {
  subjects: BlogSubject[];
}

/**
 * `GET /api/v1/public/blogs/subjects/{slug}` — one subject's blogs.
 * ``subject.blog_count`` is the FULL qualifying count, not this page's length.
 */
export interface BlogSubjectFeedResponse {
  subject: BlogSubject;
  blogs: PublicBlogCard[];
}

/**
 * `GET /api/v1/public/blogs/{slug}` — one blog, the CURRENT version.
 *
 * ⚠ ``is_public`` is part of the contract, not decoration. This endpoint also
 * serves RETRACTED blogs (`is_public=false`): retraction delists, it does not
 * delete and does not unpublish, so the URL keeps resolving for anyone holding
 * the link (plan §5). The page reads this flag to set `robots: noindex` (§7) —
 * that, and not a 404, is what deindexes it.
 *
 * ``references`` reuses the relational `Reference` type, exactly like
 * `BlogPostPublic` above, so `ReferencePanel` renders both surfaces unchanged.
 */
export interface PublicBlogDetail {
  is_public: boolean;
  slug: string;
  title: string;
  type: string;
  subjects: BlogSubjectRef[];
  content_md: string;
  references: Reference[];
  /** ANONYMIZED question the article was generated from. Never rendered. */
  question_text: string;
  created_at: string;
  updated_at: string | null;
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
   * RETIRED with the 4-step «اتعرف على ريحان» tour (2026-10-21). Old rows
   * still carry it; nothing reads or writes it any more.
   */
  onboarding_seen?: boolean;
  /**
   * The one re-ask of the profession question for users who answered
   * «declined» has happened (picked OR dismissed). Absent/false + a stored
   * `declined` → the profession step opens once more.
   */
  profession_reask_done?: boolean;
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
  /** ``null`` for system templates (قالب عام). */
  user_id: string | null;
  title: string;
  content_md: string;
  /** ``"system"`` for system templates. */
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

export type {
  SSEAgentRunStarted,
  DeepSearchStage,
  SSEAgentProgressData,
  SSEAgentProgress,
  SSEStatus,
  SSEAgentRunFinished,
  SSEAgentQuestion,
  SSEAgentResumed,
  SSEWorkspaceItemCreated,
  SSEWorkspaceItemUpdated,
  SSEWorkspaceItemLocked,
  SSEWorkspaceItemUnlocked,
  SSEReferencedExistingItem,
  SSENextSteps,
  SSETemplatesUsed,
} from "@/lib/contracts";

// ==========================================
// SEND MESSAGE PAYLOAD
// ==========================================

export type {
  SendMessagePayload,
} from "@/lib/contracts";

// ==========================================
// ON-DEMAND REFERENCE SOURCE REVEAL (access-tiers Phase C, §6.2)
// ==========================================

export type {
  ReferenceUnlockReason,
  ReferenceUnlockInfo,
  ReferenceBalance,
  ReferenceSourceResponse,
  ReferenceSourceError,
} from "@/lib/contracts";

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
   * exactly.
   *
   * It gates tokenization on BOTH checkout paths, and has since 2026-09-23:
   * `credit_card.save_card` on this alone, and `apple_pay.save_card` on this
   * AND the Apple Pay capability gate (the wallet config only exists when
   * Apple Pay is actually being offered). Until that date it gated the card
   * path only — the wallet spread carried no tokenization key at all, so every
   * Apple Pay buyer on pro/max silently never auto-renewed. If you add a third
   * payment method, this flag is the thing it has to consult.
   */
  requires_recurring_consent: boolean;
  /**
   * عرض الشهر الأول — set when THIS checkout was priced by the profession intro
   * offer (`.claude/plans/profession_intro_offer.md`); null/absent otherwise.
   * `amount_halalas` already IS the first-month price — this block only lets
   * the page explain the number and the step-up that follows it.
   */
  intro_offer?: CheckoutIntroOffer | null;
  /** The first month of the intro offer cannot be refunded. Absent = false. */
  non_refundable?: boolean;
}

/**
 * The priced intro offer on a checkout row. 2-dp LATIN strings like every other
 * money field. `then_sar` is the early-adopter seat price the next
 * `then_months` renewals are charged; `list_sar` is what follows them.
 */
export interface CheckoutIntroOffer {
  profession_group: string;
  first_month_sar: string;
  then_sar: string;
  then_months: number;
  list_sar: string;
}

/**
 * `GET /payments/intro-offer` — the CALLER's eligibility for عرض الشهر الأول.
 *
 * Server-decided end to end: the client never names a price, and an ineligible
 * answer carries nulls. ⚠ No end date and no seat count — same rule as the
 * early-adopter campaign; «المقاعد محدودة» is the only scarcity signal.
 */
export interface IntroOfferState {
  eligible: boolean;
  profession_group: string | null;
  first_month_sar: string | null;
  then_sar: string | null;
  then_months: number;
  list_sar: string | null;
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
  /** The intro offer's first month — never refundable (`refundable` is false
   *  for it too; this says WHY, so the row can explain the missing button). */
  non_refundable?: boolean;
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
  /**
   * How the credential was created — `creditcard` for a PAN typed into the
   * form, `applepay` / `samsungpay` for a wallet token.
   *
   * ⚠ `brand` + `last4` describe the FUNDING card in every case (Moyasar
   * returns the FPAN, not the device PAN), so a wallet credential must be
   * rendered as «Apple Pay» **plus** its funding card — never as a bare card
   * brand, which is what a wallet used to silently claim to be.
   *
   * `null` means unknown and is NOT recoverable: rows stored before the
   * provenance column existed can never be classified retroactively. Optional
   * for deploy skew — a backend that predates the field omits it entirely, and
   * `undefined` must read exactly like `null` (see `early_adopter`).
   */
  source_type?: "creditcard" | "applepay" | "samsungpay" | null;
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
  /**
   * The caller once paid an intro-offer first month, so a cancellation does NOT
   * forfeit the early-adopter price for them: coming back while the campaign is
   * open costs the promo price again with a fresh 90 days. Absent = false.
   */
  returnable?: boolean;
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
   * which is the one place that rule is stated before the user acts — or, for
   * a `returnable` intro-offer member, that they may come back at it.
   */
  early_adopter?: EarlyAdopterState | null;
}
