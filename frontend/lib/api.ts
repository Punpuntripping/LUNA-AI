import type {
  AuthResponse,
  AuthTokens,
  User,
  CaseListResponse,
  CaseDetailResponse,
  CreateCaseRequest,
  CreateCaseResponse,
  CaseDetail,
  ConversationListResponse,
  ConversationDetail,
  CreateConversationRequest,
  MessageListResponse,
  Document,
  DocumentListResponse,
  DownloadResponse,
  Memory,
  MemoryListResponse,
  Reference,
  WorkspaceItem,
  WorkspaceItemListResponse,
  CreateNoteRequest,
  CreateReferenceRequest,
  AttachFromDocumentRequest,
  UpdateVisibilityRequest,
  UpdateWorkspaceItemRequest,
  WorkspaceFeedback,
  WorkspaceFileUrlResponse,
  UserPreferences,
  UserPreferencesData,
  UploadInitResponse,
  UserTemplate,
  CreateTemplateRequest,
  UpdateTemplateRequest,
  TemplateListResponse,
  TemplateIngestResponse,
  UsageReport,
  RedeemCodeResponse,
  ShareDraftResponse,
  ShareArtifactResponse,
  PublicBlogsResponse,
  MyBlogsResponse,
  ImportBlogResponse,
  BlogItemResponse,
  BlogPostPublic,
  ReferenceSourceResponse,
  ReferenceSourceError,
  PaymentCheckoutResponse,
  PaymentVerifyResponse,
  PaymentHistoryResponse,
  PaymentRefundResponse,
  SubscriptionState,
  CancelSubscriptionReason,
} from "@/types";
import { supabase } from "@/lib/supabase";
import { loginHref } from "@/lib/safe-next";
// Access-tiers Phase C: the metered reference reveal answers with the SAME D14
// 402 body as `/library/full/*`, so it reuses that module's defensive parser
// rather than growing a second one that could drift from it.
import { parseRefusal, type LibraryRefusal } from "@/lib/library/full-content";

interface ApiErrorNested {
  code: string;
  message: string;
  status: number;
}

interface ApiErrorBody {
  error?: ApiErrorNested;
  detail?: string;
  code?: string;
  status?: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_PREFIX = "/api/v1";

// Expose the resolved API base so UI badges (and curious devs in DevTools)
// can confirm which backend the bundle is wired to. Without this it's easy
// to think you're on Railway while actually hitting localhost (the trap
// that surfaced during conv 79970da4 monitoring on 2026-06-01).
export function getApiBase(): string {
  return API_BASE;
}

// Print the wiring once on first browser load so a DevTools console glance
// always answers "which backend is this?". Server-side renders skip it.
if (typeof window !== "undefined") {
  // eslint-disable-next-line no-console
  console.info(
    `%c[Luna] API base: ${API_BASE}`,
    "color:#9b5c8a;font-weight:600",
  );
}

// -----------------------------------------------
// Token management (access token in MEMORY only)
// Refresh token is managed by Supabase via HttpOnly
// cookie through @supabase/ssr — never in localStorage.
// -----------------------------------------------

let accessToken: string | null = null;

export function setTokens(tokens: AuthTokens): void {
  accessToken = tokens.access_token;
  // The refresh token isn't kept here. The browser supabase client owns
  // it — callers that obtain tokens from the backend MUST seed the client
  // via supabase.auth.setSession(...) (see auth-store.login) so the
  // sb-<ref>-auth-token cookie gets written. Without that, new tabs have
  // no session to hydrate from and AuthGuard kicks the user to /login.
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function clearTokens(): void {
  accessToken = null;
}

// -----------------------------------------------
// Refresh logic (via Supabase SSR session refresh)
// -----------------------------------------------

let refreshPromise: Promise<string> | null = null;

/** Hard-eject to /login, carrying the current page as `?next=` so re-login
 *  puts the user straight back. `loginHref` runs the value through `safeNext`,
 *  so a page outside the allowlist degrades to a plain `/login`. */
function ejectToLogin(): void {
  window.location.href = loginHref(
    `${window.location.pathname}${window.location.search}`,
  );
}

async function refreshAccessToken(): Promise<string> {
  // Deduplicate concurrent refresh attempts
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    // Use Supabase client to refresh the session.
    // @supabase/ssr manages the refresh token in an HttpOnly cookie,
    // so we don't need to pass it manually.
    const { data, error } = await supabase.auth.refreshSession();
    let session = data?.session ?? null;

    // A refreshSession() error is NOT proof the session is dead. Refresh
    // tokens are single-use, so a concurrent refresh (another tab, the
    // proactive timer, Supabase's own auto-refresh) may have already rotated
    // the token — failing this call with "Already Used" while the session is
    // perfectly alive. Confirm via getSession() (which refreshes an expired
    // token itself when needed) before ejecting the user — the same
    // double-check auth-store.loadUser / revalidateSession already do.
    if (error || !session) {
      const { data: current } = await supabase.auth.getSession();
      session = current?.session ?? null;
    }

    if (!session) {
      clearTokens();
      ejectToLogin();
      throw new Error("Token refresh failed");
    }

    accessToken = session.access_token;
    return session.access_token;
  })();

  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

// -----------------------------------------------
// Core fetch wrapper with auto-retry on 401
// -----------------------------------------------

export class ApiClientError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  retry = true
): Promise<T> {
  const url = `${API_BASE}${API_PREFIX}${path}`;
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  // Don't set Content-Type for FormData (browser sets boundary automatically)
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }

  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const res = await fetch(url, { ...options, headers });

  // Handle 401: attempt token refresh once (only if we had a token — skip for login/register)
  if (res.status === 401 && retry && accessToken) {
    try {
      await refreshAccessToken();
      return apiFetch<T>(path, options, false);
    } catch {
      clearTokens();
      ejectToLogin();
      throw new ApiClientError(401, "unauthorized", "Session expired");
    }
  }

  if (!res.ok) {
    let errorBody: ApiErrorBody;
    try {
      errorBody = await res.json();
    } catch {
      errorBody = {
        detail: res.statusText,
        code: "unknown",
        status: res.status,
      };
    }
    // Support nested format: {"error": {"code": "...", "message": "...", "status": N}, "detail": "..."}
    // Fall back to flat format: {"code": "...", "detail": "..."}
    throw new ApiClientError(
      res.status,
      errorBody.error?.code || errorBody.code || "unknown",
      errorBody.error?.message || errorBody.detail || "Request failed"
    );
  }

  // Handle 204 No Content
  if (res.status === 204) return {} as T;

  return res.json();
}

// -----------------------------------------------
// SEO Library — authed claim + forms→writer handoff
// -----------------------------------------------

/** Full anon answer revealed to the authed owner (`POST /ask/claim`). */
export interface AskClaimResponse {
  question: string;
  answer_md: string;
  page_type: string;
  page_id: string;
}

/** The freshly-copied قوالبي template from the forms→writer handoff. */
export interface OpenInWriterResult {
  template_id: string;
  title: string;
}

// -----------------------------------------------
// Convenience methods
// -----------------------------------------------

export const api = {
  get: <T>(path: string) => apiFetch<T>(path, { method: "GET" }),

  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),

  patch: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),

  upload: <T>(path: string, formData: FormData) =>
    apiFetch<T>(path, {
      method: "POST",
      body: formData,
      // Content-Type is NOT set — browser adds multipart boundary
    }),

  // -----------------------------------------------
  // Blog / public share-by-link (مدونة)
  // -----------------------------------------------
  // The PUBLIC read (``GET /public/blog/{token}``) is fetched server-side in
  // app/blog/[token]/page.tsx with a plain ``fetch`` (no auth header), NOT via
  // these helpers. These three are the AUTHED owner-side actions and go
  // through apiFetch (Bearer token + 401 retry + Arabic error mapping).

  /** Pre-fill the share dialog: derive the default السؤال for an artifact. */
  getShareDraft: (itemId: string) =>
    apiFetch<ShareDraftResponse>(`/workspace/${itemId}/share-draft`, {
      method: "GET",
    }),

  /**
   * Publish an artifact → mint an unguessable public URL. ``displayMode``
   * selects the share template: ``"question"`` (السؤال page, ``title`` null)
   * or ``"title"`` (editorial article, ``title`` required server-side).
   */
  shareArtifact: (
    itemId: string,
    args: { questionText: string; displayMode: "question" | "title"; title: string | null },
  ) =>
    apiFetch<ShareArtifactResponse>(`/workspace/${itemId}/share`, {
      method: "POST",
      body: JSON.stringify({
        question_text: args.questionText,
        display_mode: args.displayMode,
        title: args.title,
      }),
    }),

  /**
   * List the PUBLIC ``/blog`` gallery (``is_public`` posts, newest first).
   * Anonymous — no auth required; SEO-indexable. (v2: replaces the v1 gated
   * ``/blog/directory``; the curate gate moved onto publish/unpublish.)
   */
  listPublicBlogs: () =>
    apiFetch<PublicBlogsResponse>(`/public/blogs`, { method: "GET" }),

  /**
   * List مدوناتي — the caller's own blog_posts (both templates, owner-scoped).
   * ``can_publish_public`` mirrors ``users.can_access_blog``: when true the
   * management page exposes the نشر في المدونة العامة toggle.
   *
   * ``q`` ranks the SAME response shape through ``bm25_search()``
   * (bm25_navigation_search.md §5.2) instead of newest-first — the endpoint and
   * the payload are unchanged, which is the whole point of D8: the grid keeps
   * rendering the card it already renders. Owner scoping happens twice
   * server-side (the RPC's ``p_owner`` and the id filter), never here. A term
   * shorter than 3 characters is a 400 in Arabic, so callers gate on
   * ``useSearchQuery`` and never send one.
   */
  listMyBlogs: (q?: string) => {
    const term = (q ?? "").trim();
    const suffix = term ? `?q=${encodeURIComponent(term)}` : "";
    return apiFetch<MyBlogsResponse>(`/blogs/mine${suffix}`, { method: "GET" });
  },

  /**
   * Curate a post INTO the public gallery (``is_public = true``). Authed +
   * gated server-side on ``users.can_access_blog`` (403 otherwise).
   */
  publishBlogPublic: (postId: string) =>
    apiFetch<{ success: boolean }>(`/blogs/${postId}/publish`, {
      method: "POST",
    }),

  /** Remove a post FROM the public gallery (``is_public = false``). */
  unpublishBlogPublic: (postId: string) =>
    apiFetch<{ success: boolean }>(`/blogs/${postId}/publish`, {
      method: "DELETE",
    }),

  /** Owner kill-switch: revoke a published post (leaked-link mitigation). */
  unpublishPost: (postId: string) =>
    apiFetch<{ success: boolean }>(`/blog/posts/${postId}`, {
      method: "DELETE",
    }),

  // -----------------------------------------------
  // Blog import (.claude/plans/blog_import.md)
  // -----------------------------------------------

  /**
   * Save a pasted share link/token into مدوناتي as a snapshot copy owned by
   * the caller (own fresh token, never auto-public). Backend tolerantly
   * extracts the token from a full URL or a bare 32-hex string. Idempotent
   * per root post (``already_saved``).
   */
  importBlog: (tokenOrUrl: string) =>
    apiFetch<ImportBlogResponse>(`/blogs/import`, {
      method: "POST",
      body: JSON.stringify({ token: tokenOrUrl }),
    }),

  /**
   * Copy the blog snapshot behind a token into a conversation as a
   * ``kind='agent_search'`` workspace item — تحليل قانوني with a real
   * المراجع panel («اتحدث مع المدونة» / composer paste-chip). Idempotent per
   * conversation+root post (``already_attached``).
   */
  createBlogItem: (conversationId: string, tokenOrUrl: string) =>
    apiFetch<BlogItemResponse>(`/conversations/${conversationId}/blog-items`, {
      method: "POST",
      body: JSON.stringify({ token: tokenOrUrl }),
    }),

  /**
   * Client-side read of a public post (anonymous endpoint) — used by the
   * composer paste-chip to show the blog title before send. The server-side
   * blog pages keep their own plain ``fetch``; this one rides apiFetch for
   * the error mapping (a Bearer header on a public endpoint is harmless).
   */
  getPublicBlog: (token: string) =>
    apiFetch<BlogPostPublic>(`/public/blog/${token}`, { method: "GET" }),

  // -----------------------------------------------
  // اسأل ريحان — authed claim of an anon answer (post-signup continuity moment)
  // -----------------------------------------------

  /**
   * Claim the full answer for an anon question the caller asked before signing
   * up. AUTHED (bearer + 401 retry). The row must match id AND session_key;
   * idempotent for the same owner. Consumed by the AuthGuard `claim_anon_answer`
   * intent, which stashes the result for the widget to reveal.
   */
  claimAnonAnswer: (questionId: string, sessionKey: string) =>
    apiFetch<AskClaimResponse>(`/ask/claim`, {
      method: "POST",
      body: JSON.stringify({
        question_id: questionId,
        session_key: sessionKey,
      }),
    }),
};

// -----------------------------------------------
// Forms → writer handoff (نماذج «افتح هذا النموذج في ريحان»)
// -----------------------------------------------

export const formsApi = {
  /**
   * Copy a PUBLISHED form into the caller's قوالبي and return the new template
   * id + title. AUTHED (bearer + 401 retry). The forms→writer conversion CTA
   * (anon path: post-login intent `open_form_in_writer`) lands the user in the
   * writer at `/templates/{template_id}`.
   */
  openInWriter: (slug: string) =>
    apiFetch<OpenInWriterResult>(`/forms/${encodeURIComponent(slug)}/open-in-writer`, {
      method: "POST",
    }),
};

// -----------------------------------------------
// Auth API
// -----------------------------------------------

export const authApi = {
  login: (email: string, password: string) =>
    api.post<AuthResponse>("/auth/login", { email, password }),

  // Signup runs entirely in the browser via supabase.auth.signUp() — see
  // stores/auth-store.ts. No backend endpoint to call here. The signup
  // consent version (option B) rides along as options.data.terms_version on
  // that signUp call, NOT through this layer.

  refresh: () =>
    refreshAccessToken().then((token) => ({
      access_token: token,
      refresh_token: "", // Managed by Supabase SSR cookie
    })),

  logout: () => api.post<{ success: boolean }>("/auth/logout"),

  me: () => api.get<User>("/auth/me"),

  /** Store the onboarding profession answer (users.profession_* — migration
   *  115). The label is only kept server-side for specialist/individual. */
  updateProfession: (profession_group: string, profession_label: string | null) =>
    api.patch<{ profession_group: string; profession_label: string | null }>(
      "/auth/profession",
      { profession_group, profession_label },
    ),

  // -----------------------------------------------
  // Account settings (إعدادات الحساب)
  // -----------------------------------------------

  /** Store «بماذا تحب أن نناديك؟» (users.preferred_name — migration 122).
   *  Pass null to clear the override; the response then carries the derived
   *  default in `call_name`, so the field can refill from it. */
  updatePreferredName: (preferred_name: string | null) =>
    api.patch<{ preferred_name: string | null; call_name: string | null }>(
      "/auth/preferred-name",
      { preferred_name },
    ),

  /** Rejected with 400 VALIDATION_ERROR for Google-only accounts (no password
   *  identity); 401 AUTH_INVALID when `current_password` is wrong. */
  changePassword: (current_password: string, new_password: string) =>
    api.post<{ success: boolean }>("/auth/change-password", {
      current_password,
      new_password,
    }),

  /** Revokes ALL refresh tokens — including this device's. 503 on failure. */
  logoutAll: () => api.post<{ success: boolean }>("/auth/logout-all"),

  /** Schedules deletion (30-day grace). The server checks the real identity:
   *  password is required for email accounts (422 if missing) and ignored for
   *  Google-only accounts — the client never chooses the branch. */
  deleteAccount: (password?: string) =>
    api.post<{ success: boolean }>("/auth/delete-account", { password }),

  restoreAccount: () => api.post<{ success: boolean }>("/auth/restore-account"),
};

// -----------------------------------------------
// Cases API
// -----------------------------------------------

export const casesApi = {
  list: (params?: { status?: string; page?: number; per_page?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set("status", params.status);
    if (params?.page) searchParams.set("page", String(params.page));
    if (params?.per_page) searchParams.set("per_page", String(params.per_page));
    const qs = searchParams.toString();
    return api.get<CaseListResponse>(`/cases${qs ? `?${qs}` : ""}`);
  },

  get: (caseId: string) =>
    api.get<CaseDetailResponse>(`/cases/${caseId}`),

  create: (data: CreateCaseRequest) =>
    api.post<CreateCaseResponse>("/cases", data),

  update: (caseId: string, data: Partial<CreateCaseRequest>) =>
    api.patch<{ case: CaseDetail }>(`/cases/${caseId}`, data),

  updateStatus: (caseId: string, status: string) =>
    api.patch<{ case: CaseDetail }>(`/cases/${caseId}/status`, { status }),

  delete: (caseId: string) =>
    api.delete<{ success: boolean }>(`/cases/${caseId}`),
};

// -----------------------------------------------
// Conversations API
// -----------------------------------------------

export const conversationsApi = {
  list: (params?: {
    case_id?: string | null;
    limit?: number;
    offset?: number;
    /** Full-text query over titles + message content (server-scoped to user). */
    q?: string;
    /** When true, return only starred conversations. */
    starred?: boolean;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.case_id) searchParams.set("case_id", params.case_id);
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    if (params?.q) searchParams.set("q", params.q);
    if (params?.starred) searchParams.set("starred", "true");
    const qs = searchParams.toString();
    return api.get<ConversationListResponse>(`/conversations${qs ? `?${qs}` : ""}`);
  },

  get: (conversationId: string) =>
    api.get<{ conversation: ConversationDetail }>(`/conversations/${conversationId}`),

  create: (data: CreateConversationRequest) =>
    api.post<{ conversation: ConversationDetail }>("/conversations", data),

  /**
   * Rename and/or star a conversation. ``title_ar`` renames; ``starred`` toggles
   * the star (server stamps ``starred_at`` accordingly). At least one field must
   * be present (enforced server-side).
   */
  update: (
    conversationId: string,
    body: { title_ar?: string; starred?: boolean },
  ) =>
    api.patch<{ conversation: ConversationDetail }>(
      `/conversations/${conversationId}`,
      body,
    ),

  delete: (conversationId: string) =>
    api.delete<{ success: boolean }>(`/conversations/${conversationId}`),

  endSession: (conversationId: string) =>
    api.post<{ conversation: ConversationDetail }>(`/conversations/${conversationId}/end-session`),
};

// -----------------------------------------------
// Messages API
// -----------------------------------------------

export const messagesApi = {
  list: (conversationId: string, params?: { limit?: number; before?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.before) searchParams.set("before", params.before);
    const qs = searchParams.toString();
    return api.get<MessageListResponse>(`/conversations/${conversationId}/messages${qs ? `?${qs}` : ""}`);
  },

  /** Returns raw Response for SSE stream reading — do NOT use apiFetch.
   *  Includes 401 retry logic: if token expired, refresh and retry once. */
  send: async (
    conversationId: string,
    content: string,
    signal?: AbortSignal,
    options?: { attachment_ids?: string[] }
  ): Promise<Response> => {
    const url = `${API_BASE}${API_PREFIX}/conversations/${conversationId}/messages`;
    const doFetch = () => {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (accessToken) {
        headers["Authorization"] = `Bearer ${accessToken}`;
      }
      const body: Record<string, unknown> = { content };
      if (options?.attachment_ids?.length) body.attachment_ids = options.attachment_ids;
      return fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal,
      });
    };

    const res = await doFetch();
    if (res.status === 401 && accessToken) {
      try {
        await refreshAccessToken();
        return doFetch();
      } catch {
        clearTokens();
        ejectToLogin();
        throw new ApiClientError(401, "unauthorized", "Session expired");
      }
    }
    return res;
  },
};

// -----------------------------------------------
// Documents API
// -----------------------------------------------

export const documentsApi = {
  list: (caseId: string, params?: { page?: number; limit?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.page) searchParams.set("page", String(params.page));
    if (params?.limit) searchParams.set("limit", String(params.limit));
    const qs = searchParams.toString();
    // TODO(upload-reliability): pre-existing template-literal typo audited
    // here — backslashes in the path were silently producing `\cases\…` in
    // some historical builds. Verified the template is correct as written;
    // leaving the marker so a future cleanup can drop it without re-auditing.
    return api.get<DocumentListResponse>(`/cases/${caseId}/documents${qs ? `?${qs}` : ""}`);
  },

  /**
   * @deprecated Use `initUpload` + tus + `finalizeUpload` instead.
   * Kept for 7 days post-deploy so we can roll back if the new flow
   * regresses. Slated for removal in Phase 4 (`.claude/plans/upload_reliability.md`).
   */
  upload: (caseId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.upload<Document>(`/cases/${caseId}/documents`, formData);
  },

  /** Phase 2: mint a TUS upload URL for a new case document. */
  initUpload: (
    caseId: string,
    body: { filename: string; mime_type: string; size_bytes: number },
  ) =>
    api.post<UploadInitResponse>(`/cases/${caseId}/documents/init`, body),

  /** Phase 2: verify bytes landed, flip status to `pending`, enqueue OCR. */
  finalizeUpload: (documentId: string) =>
    api.post<Document>(`/documents/${documentId}/finalize`, {}),

  /** Phase 2: user cancelled — soft-delete row + best-effort delete object. */
  cancelUpload: (documentId: string) =>
    api.post<{ success: boolean }>(`/documents/${documentId}/cancel`, {}),

  get: (documentId: string) =>
    api.get<Document>(`/documents/${documentId}`),

  download: (documentId: string) =>
    api.get<DownloadResponse>(`/documents/${documentId}/download`),

  delete: (documentId: string) =>
    api.delete<{ success: boolean }>(`/documents/${documentId}`),
};

// -----------------------------------------------
// Memories API
// -----------------------------------------------

export const memoriesApi = {
  list: (caseId: string, params?: { type?: string; page?: number; limit?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.type) searchParams.set("type", params.type);
    if (params?.page) searchParams.set("page", String(params.page));
    if (params?.limit) searchParams.set("limit", String(params.limit));
    const qs = searchParams.toString();
    return api.get<MemoryListResponse>(`/cases/${caseId}/memories${qs ? `?${qs}` : ""}`);
  },

  create: (caseId: string, body: { memory_type: string; content_ar: string }) =>
    api.post<Memory>(`/cases/${caseId}/memories`, body),

  update: (memoryId: string, body: { content_ar?: string; memory_type?: string }) =>
    api.patch<Memory>(`/memories/${memoryId}`, body),

  delete: (memoryId: string) =>
    api.delete<{ success: boolean }>(`/memories/${memoryId}`),
};

// -----------------------------------------------
// Workspace API
// -----------------------------------------------

export const workspaceApi = {
  listByConversation: (conversationId: string) =>
    api.get<WorkspaceItemListResponse>(`/conversations/${conversationId}/workspace`),

  listByCase: (caseId: string) =>
    api.get<WorkspaceItemListResponse>(`/cases/${caseId}/workspace`),

  get: (itemId: string) =>
    api.get<WorkspaceItem>(`/workspace/${itemId}`),

  update: (itemId: string, data: UpdateWorkspaceItemRequest) =>
    api.patch<WorkspaceItem>(`/workspace/${itemId}`, data),

  delete: (itemId: string) =>
    api.delete<{ success: boolean }>(`/workspace/${itemId}`),

  setVisibility: (itemId: string, body: UpdateVisibilityRequest) =>
    api.patch<WorkspaceItem>(`/workspace/${itemId}/visibility`, body),

  setFeedback: (itemId: string, feedback: WorkspaceFeedback) =>
    api.patch<WorkspaceItem>(`/workspace/${itemId}/feedback`, { feedback }),

  fileUrl: (itemId: string) =>
    api.get<WorkspaceFileUrlResponse>(`/workspace/${itemId}/file`),

  createNote: (conversationId: string, body: CreateNoteRequest) =>
    api.post<WorkspaceItem>(
      `/conversations/${conversationId}/workspace/notes`,
      body,
    ),

  createReference: (conversationId: string, body: CreateReferenceRequest) =>
    api.post<WorkspaceItem>(
      `/conversations/${conversationId}/workspace/references`,
      body,
    ),

  /**
   * @deprecated Use `initAttachment` + tus + `finalizeAttachment` instead.
   * Kept for 7 days post-deploy alongside `documentsApi.upload` so we can
   * roll back. Slated for removal in Phase 4.
   */
  uploadAttachment: (conversationId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.upload<WorkspaceItem>(
      `/conversations/${conversationId}/workspace/attachments/upload`,
      formData,
    );
  },

  /** Phase 2: mint a TUS upload URL for a new chat attachment. */
  initAttachment: (
    conversationId: string,
    body: {
      filename: string;
      mime_type: string;
      size_bytes: number;
      /** Client page estimate (PDF parsed in-browser; image → 1) for the OCR gate. */
      page_count?: number;
    },
  ) =>
    api.post<UploadInitResponse>(
      `/conversations/${conversationId}/workspace/attachments/init`,
      body,
    ),

  /** Phase 2: verify bytes landed; backend returns the materialised row. */
  finalizeAttachment: (itemId: string) =>
    api.post<WorkspaceItem>(`/workspace/attachments/${itemId}/finalize`, {}),

  /** Phase 2: user cancelled mid-upload. Idempotent on the server. */
  cancelAttachment: (itemId: string) =>
    api.post<{ success: boolean }>(
      `/workspace/attachments/${itemId}/cancel`,
      {},
    ),

  attachFromDocument: (
    conversationId: string,
    body: AttachFromDocumentRequest,
  ) =>
    api.post<WorkspaceItem>(
      `/conversations/${conversationId}/workspace/attachments/from-document`,
      body,
    ),

  /**
   * Migration 049: fetch the per-WI reference list. References used to live
   * on ``metadata.references`` JSONB; they now live in a relational table
   * and are reconstructed by the backend via JOINs to chunks_v2 / cases /
   * services. Response shape matches the pre-049 ``Reference[]`` so the
   * existing ReferencePanel renders identically.
   */
  listReferences: (itemId: string, opts?: { usedOnly?: boolean }) => {
    const qs = opts?.usedOnly ? "?used=true" : "";
    return api.get<{ references: Reference[] }>(
      `/workspace/${itemId}/references${qs}`,
    );
  },

  /**
   * ACCESS-TIERS PHASE C — reveal ONE reference's original source (metered).
   *
   * The references list above ships the citation mesh only; this is where the
   * body lives now, and where the charge sits. CALL ONLY FROM A USER GESTURE
   * (a `[n]` click or «عرض المصدر»): fetching on mount would spend an unlock
   * for every card in the panel, which is exactly the §5.1 "trick" feeling.
   *
   * Deliberately NOT routed through `apiFetch`, for two reasons:
   *  1. `ApiClientError` keeps only `status`/`code`/`message`, and every
   *     refusal shares one code (`LIBRARY_QUOTA_EXCEEDED`) — the fields that
   *     pick the card (`reason`, `resets_at`, `stored_count`) would be thrown
   *     away, making an exhausted period indistinguishable from a frozen shelf.
   *  2. `apiFetch` redirects to /login on a dead session. Here that would eject
   *     a lawyer mid-read over one optional dialog; the caller renders
   *     «انتهت جلستك» with a login CTA instead. The panel's other requests
   *     (the references list) still own the refresh-and-retry path.
   *
   * Returns a discriminated union — a refusal and a 429 are ANSWERS, not
   * exceptions, so neither is retried by TanStack Query as a failure.
   */
  getReferenceSource: (itemId: string, n: number) =>
    fetchReferenceSource(
      `/workspace/${encodeURIComponent(itemId)}/references/${n}/source`,
      { requireToken: true },
    ),
};

/**
 * Shared transport for BOTH reference-reveal endpoints — the in-app workspace
 * one and the public-blog one. One implementation so the two surfaces cannot
 * drift on how a 402 / 404 / 429 is classified.
 *
 * `requireToken: false` is what makes the blog surface work for a logged-out
 * reader: the request goes out WITHOUT an Authorization header, the endpoint's
 * optional-auth dependency resolves the caller as anonymous, and the server
 * answers 402 `reason='anonymous'` — which renders as «سجّل مجاناً لعرض المصدر».
 * Refusing client-side instead would duplicate entitlement policy in the
 * browser, and the server is the only place that should decide it.
 *
 * Plain `fetch`, never `apiFetch`: a dead session must not trigger the global
 * redirect-to-login while someone is reading a public blog post.
 */
async function fetchReferenceSource(
  path: string,
  { requireToken }: { requireToken: boolean },
): Promise<ReferenceSourceResult> {
  const token = getAccessToken();
  if (!token && requireToken) {
    return { ok: false, kind: "error", error: "no_token", status: null };
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${API_PREFIX}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    return { ok: false, kind: "error", error: "network", status: null };
  }

  if (res.status === 402) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    return { ok: false, kind: "refusal", refusal: parseRefusal(body) };
  }

  if (!res.ok) {
    const error: ReferenceSourceError =
      res.status === 401 || res.status === 403
        ? "unauthorized"
        : res.status === 404
          ? "not_found"
          : res.status === 429
            ? "rate_limited"
            : "server";
    return { ok: false, kind: "error", error, status: res.status };
  }

  try {
    return { ok: true, data: (await res.json()) as ReferenceSourceResponse };
  } catch {
    return { ok: false, kind: "error", error: "network", status: res.status };
  }
}

/**
 * Reveal one cited source on a PUBLIC blog post.
 *
 * Addressed by `(blog token, n)` rather than a workspace item id: the reader is
 * not the author, so the workspace endpoint's ownership check would 404 them.
 * The post's unguessable token is the capability — it already grants the page.
 */
export const publicBlogApi = {
  getReferenceSource: (blogToken: string, n: number) =>
    fetchReferenceSource(
      `/public/blog/${encodeURIComponent(blogToken)}/references/${n}/source`,
      { requireToken: false },
    ),
};

/**
 * Outcome of `workspaceApi.getReferenceSource`. Narrow on `ok`, then `kind`:
 *   { ok: true,  data }                         → render the source
 *   { ok: false, kind: "refusal", refusal }     → the D14 refusal card
 *   { ok: false, kind: "error", error, status } → transport / session / 429
 */
export type ReferenceSourceResult =
  | { ok: true; data: ReferenceSourceResponse }
  | { ok: false; kind: "refusal"; refusal: LibraryRefusal }
  | {
      ok: false;
      kind: "error";
      error: ReferenceSourceError;
      status: number | null;
    };

// -----------------------------------------------
// Preferences API
// -----------------------------------------------

export const preferencesApi = {
  get: () => api.get<UserPreferences>("/preferences"),

  update: (preferences: UserPreferencesData) =>
    api.patch<UserPreferences>("/preferences", { preferences }),
};

// -----------------------------------------------
// Templates API (قوالبي) — user-global markdown docs
// -----------------------------------------------

// -----------------------------------------------
// Usage limits API — read-only snapshot for the Settings dialog
// -----------------------------------------------

export const usageApi = {
  get: () => api.get<UsageReport>("/usage"),
};

// -----------------------------------------------
// Plans API — activation-code redemption
// -----------------------------------------------

export const plansApi = {
  redeem: (code: string) =>
    api.post<RedeemCodeResponse>("/plans/redeem", { code }),
};

// -----------------------------------------------
// Payments API — Moyasar one-time checkout (Wave 1)
// (.claude/plans/moyasar_payments.md Phases C + D)
// -----------------------------------------------

export const paymentsApi = {
  /**
   * Open a checkout. The body is `{plan_id}` and NOTHING ELSE — the amount, the
   * VAT split and any prorated upgrade credit are all computed server-side from
   * `plans.price_sar`. Never add an amount parameter here.
   *
   * Errors arrive as `ApiClientError` with the backend's Arabic message:
   * `PAYMENT_PLAN_NOT_PURCHASABLE` (downgrade blocked / plan has no price),
   * `PAYMENT_PROVIDER_ERROR`, or a 503 when `MOYASAR_SECRET_KEY` is unset
   * (fail-closed posture).
   */
  checkout: (planId: string) =>
    api.post<PaymentCheckoutResponse>("/payments/checkout", { plan_id: planId }),

  /**
   * Sync our row against `GET /v1/payments/{id}` at Moyasar. Called from two
   * moments — the form's pre-3DS `on_completed`, and the callback page — so a
   * `pending` answer is normal, not a failure.
   *
   * `moyasar_id` is attacker-controllable (it arrives on the redirect query
   * string), which is exactly why the server binds it back to our row via
   * `metadata.payment_id` AND the caller's `user_id` before trusting it
   * (plan trap 6). Nothing here may assume otherwise.
   */
  verify: (moyasarId: string) =>
    api.post<PaymentVerifyResponse>("/payments/verify", {
      moyasar_id: moyasarId,
    }),

  /** The caller's own receipts (سجل المدفوعات). RLS scopes it server-side. */
  history: () => api.get<PaymentHistoryResponse>("/payments/history"),

  /**
   * Self-serve refund inside the 24-hour window. The processing fee is applied
   * server-side and is deliberately NOT a parameter — a client-supplied fee is
   * a client-supplied price (plan trap 12). A request outside the window comes
   * back as `PAYMENT_REFUND_WINDOW_CLOSED` with an Arabic message.
   */
  refund: (paymentId: string) =>
    api.post<PaymentRefundResponse>(`/payments/${paymentId}/refund`),

  /**
   * Current subscription state for إعدادات الحساب: plan, term end, `source`,
   * and whether the renewal opt-out applies.
   *
   * Separate from `usageApi.get()` on purpose — the quota report carries no
   * `source`, and it is read on the message path by every send, so a
   * money-shaped field does not belong on it.
   */
  getSubscription: () => api.get<SubscriptionState>("/payments/subscription"),

  /**
   * Opt out of renewal + record the exit survey. `reason` is REQUIRED (one of
   * the four keys), `comment` optional for every reason.
   *
   * ⚠ This is NOT a refund and NOT an early termination: the current term is
   * untouched and access runs to `expires_at`. It also stops no automatic
   * charge — Wave 1 has none. Returns the refreshed `SubscriptionState`.
   *
   * Errors arrive as `ApiClientError` with the backend's Arabic message:
   * `SUBSCRIPTION_NOT_CANCELLABLE` (no paid running term),
   * `SUBSCRIPTION_ALREADY_CANCELLED` (a second call — the server never writes
   * a second survey row), `VALIDATION_ERROR` (unknown reason).
   */
  cancelSubscription: (body: {
    reason: CancelSubscriptionReason;
    comment?: string;
  }) => api.post<SubscriptionState>("/payments/subscription/cancel", body),

  /**
   * Undo a cancellation («تراجع عن الإلغاء»). Free — no money moved either way.
   * `SUBSCRIPTION_NOT_CANCELLABLE` comes back when there is nothing to undo or
   * the term has already ended (a lapsed plan returns only via a new purchase).
   */
  reactivateSubscription: () =>
    api.post<SubscriptionState>("/payments/subscription/reactivate"),
};

export const templatesApi = {
  /**
   * قوالبي, newest-updated first — or, with ``q``, the same list ranked by
   * ``bm25_search()`` (bm25_navigation_search.md §5.2). Templates are indexed
   * title + ``content_md`` in full (the caller's own text, nothing gated), so a
   * search reaches a clause the reader remembers writing and not just the title
   * they gave it. ONE endpoint serves both `/templates` and `/templates/mine`.
   */
  list: (q?: string) => {
    const term = (q ?? "").trim();
    const suffix = term ? `?q=${encodeURIComponent(term)}` : "";
    return api.get<TemplateListResponse>(`/templates${suffix}`);
  },

  get: (templateId: string) =>
    api.get<UserTemplate>(`/templates/${templateId}`),

  create: (body: CreateTemplateRequest) =>
    api.post<UserTemplate>("/templates", body),

  update: (templateId: string, body: UpdateTemplateRequest) =>
    api.patch<UserTemplate>(`/templates/${templateId}`, body),

  delete: (templateId: string) =>
    api.delete<{ success: boolean }>(`/templates/${templateId}`),

  // Wave E (writer_planner_user_templates): ingest an attached
  // workspace_item into a cleaned قوالبي template via the dedicated
  // template_ingester endpoint. The backend returns HTTP 200 with
  // ``{ ok: true | false }`` for both outcomes, so apiFetch does NOT throw
  // on a logical failure — the caller branches on ``result.ok``.
  ingest: (itemId: string) =>
    api.post<TemplateIngestResponse>("/templates/ingest", { item_id: itemId }),
};

// -----------------------------------------------
// «مكتبتي» API — the user's library shelf
// (.claude/plans/access_tiers_gating.md PART 5B)
// -----------------------------------------------
//
// Wire types live HERE rather than in types/index.ts on purpose: this whole
// block is appended as one self-contained unit (D17 file-ownership note), and
// the shelf shapes have exactly one consumer (hooks/use-my-library.ts →
// components/library/mine/*).
//
// Every endpoint is authed and answers `Cache-Control: private, no-store` —
// a shelf is per-user by definition and must never reach a shared/ISR cache.

/** Shelf content types. `article` never gets its own tab — مواد nest. */
export type MyLibraryContentType =
  | "regulation"
  | "article"
  | "judgment"
  | "circular"
  | "service"
  | "form"
  | "calculator";

/** Ranking. Vocabulary is USAGE («استخدام»), never «فتح» (§5B.3). */
export type MyLibrarySort = "recent" | "most_used" | "saved";

/** Which item a write endpoint is about — by canonical id, or by public slug. */
export interface MyLibraryItemRef {
  content_type: MyLibraryContentType;
  /** Canonical key: corpus uuid, or `'{regulation_id}#{article_no}'` for a مادة. */
  content_id?: string;
  /** Public page slug — resolved server-side when no `content_id` is known. */
  slug?: string;
  /** A مادة additionally needs its نظام's slug («المادة-74» repeats across statutes). */
  parent_slug?: string;
}

/** Shelf state carried by every row (and every nested مادة). */
interface MyLibraryShelfState {
  /** `'auto'` = opened · `'manual'` = pinned · `null` = synthesized نظام header. */
  source: "auto" | "manual" | null;
  use_count: number;
  first_used_at: string | null;
  last_used_at: string | null;
  saved_at: string | null;
  /** A `library_unlocks` row exists (a مادة also counts its parent نظام's row). */
  was_unlocked: boolean;
  /** That row exists but the §1.2 access predicate now fails → lock badge. */
  is_frozen: boolean;
  /** False = a نظام header synthesized purely to hold orphan مواد. */
  is_shelf_row: boolean;
  /** A public URL resolved. False ⇒ still listed, just not linkable. */
  is_available: boolean;
}

/** A مادة nested under its parent نظام — never a top-level row (§5B.1). */
export interface MyLibraryArticle extends MyLibraryShelfState {
  content_type: "article";
  content_id: string;
  slug: string | null;
  url: string | null;
  title: string;
  article_no: number | null;
  article_label: string | null;
  reg_slug: string | null;
}

/**
 * One shelf row: the public hub card fields (same names the hubs use, so the
 * existing card components take the row directly) plus the shelf state.
 * Only the fields belonging to `content_type` are populated.
 */
export interface MyLibraryRow extends MyLibraryShelfState {
  content_type: MyLibraryContentType;
  content_id: string;
  slug: string | null;
  url: string | null;
  title: string;

  // regulation
  entity_name?: string | null;
  status?: string | null;
  doc_type?: string | null;
  summary_snippet?: string | null;
  sectors?: string[] | null;
  // judgment
  court?: string | null;
  court_level?: string | null;
  court_level_label?: string | null;
  city?: string | null;
  date_hijri?: string | null;
  date_gregorian?: string | null;
  domains?: string[] | null;
  snippet?: string | null;
  // circular
  source_label?: string | null;
  body_snippet?: string | null;
  body_length?: number | null;
  // service (compliance)
  provider_name?: string | null;
  is_most_used?: boolean | null;
  intro_snippet?: string | null;
  // form
  category?: string | null;
  use_case_snippet?: string | null;
  // article (only when a مادة could NOT be nested under a نظام)
  article_no?: number | null;
  article_label?: string | null;
  reg_slug?: string | null;

  /** Self + nested مواد — what the ordering uses for a نظام group. */
  group_use_count: number;
  group_last_used_at: string | null;
  child_articles: MyLibraryArticle[];
}

/** The paged «مكتبتي» envelope (hub-shaped + the shelf totals). */
export interface MyLibraryResponse {
  items: MyLibraryRow[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  /** Echoes the filter (`article` is normalized to `regulation`). */
  content_type: MyLibraryContentType | null;
  sort: MyLibrarySort;
  /**
   * The search term the server actually APPLIED, or `null` when the listing was
   * unfiltered. Echoed so an empty page can be told apart from an empty shelf
   * without re-deriving it from local state — and so a response that arrives
   * after the box was cleared can be recognised as stale.
   */
  q?: string | null;
  /** WHOLE-shelf row count per type — drives tab visibility. */
  counts: Partial<Record<MyLibraryContentType, number>>;
  /** `library_unlocks` ROW count (the «لديك {n} مصدراً» inventory). */
  stored_library_count: number;
  /** How many of those the §1.2 predicate now fails (0 when paid). */
  frozen_count: number;
  is_paid: boolean;
}

export interface MyLibraryListParams {
  content_type?: MyLibraryContentType | null;
  sort?: MyLibrarySort;
  /**
   * BM25 over the shelf (bm25_navigation_search.md §5.2). When present it
   * REPLACES `sort` server-side — a result list is ordered by relevance — so
   * the UI hides «الترتيب» for the duration rather than showing a control the
   * server is ignoring. Same 3-character floor as everywhere else.
   */
  q?: string | null;
  page?: number;
  page_size?: number;
}

export const myLibraryApi = {
  list: (params: MyLibraryListParams = {}) => {
    const qs = new URLSearchParams();
    if (params.content_type) qs.set("content_type", params.content_type);
    if (params.sort) qs.set("sort", params.sort);
    if (params.q) qs.set("q", params.q);
    if (params.page) qs.set("page", String(params.page));
    if (params.page_size) qs.set("page_size", String(params.page_size));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return api.get<MyLibraryResponse>(`/library/mine${suffix}`);
  },

  /**
   * Record ONE use of a library item — the مكتبتي shelf beacon (204).
   *
   * Fires for GATED and OPEN items alike (D16.2, REVISED): plan §5B.2 shelves an
   * item when it is opened, "gated or not", so the page view is the use.
   * `/library/full` deliberately does NOT write to the shelf — see
   * `LibraryUseBeacon`'s docstring for why recording in both places would bias
   * «الأكثر استخداماً» toward gated content.
   *
   * ⚠ PLAIN `fetch`, NOT `apiFetch` — deliberate, and it must stay that way.
   * This beacon fires on PUBLIC library document pages. `apiFetch`'s 401 path
   * clears tokens and hard-redirects to /login (via `ejectToLogin`), so a
   * reader whose session merely went stale in a background tab would be
   * ejected off `/regulations/{slug}` by a fire-and-forget shelf write. Every sibling library
   * call avoids `apiFetch` for exactly this reason (`fetchFullContent`,
   * `fetchLibraryBalance`, `fetchAuthedHubPage`, `getReferenceSource`).
   *
   * Fire-and-forget: a failure is swallowed. A missing shelf row must never
   * disturb a content read.
   */
  recordUse: async (ref: MyLibraryItemRef): Promise<void> => {
    const token = getAccessToken();
    if (!token) return;
    try {
      await fetch(`${API_BASE}${API_PREFIX}/library/mine/use`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(ref),
      });
    } catch {
      // Swallowed on purpose — see above.
    }
  },

  /** Pin an item («حفظ») — free at every tier, grants no access (204). */
  save: (ref: MyLibraryItemRef) => api.post<void>("/library/mine/save", ref),

  /**
   * Unpin («إزالة الحفظ») — idempotent (204). Sent as query params because
   * `api.delete` carries no body; `URLSearchParams` encodes the `#` in a مادة
   * id (`{reg_id}#74`), which would otherwise be swallowed as a fragment.
   */
  unsave: (ref: MyLibraryItemRef) => {
    const qs = new URLSearchParams({ content_type: ref.content_type });
    if (ref.content_id) qs.set("content_id", ref.content_id);
    if (ref.slug) qs.set("slug", ref.slug);
    if (ref.parent_slug) qs.set("parent_slug", ref.parent_slug);
    return api.delete<void>(`/library/mine/save?${qs.toString()}`);
  },
};
