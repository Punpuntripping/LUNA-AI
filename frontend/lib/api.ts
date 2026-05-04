import type {
  AuthResponse,
  AuthTokens,
  User,
  RegisterResponse,
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
  WorkspaceItem,
  WorkspaceItemListResponse,
  CreateNoteRequest,
  CreateReferenceRequest,
  AttachFromDocumentRequest,
  UpdateVisibilityRequest,
  UpdateWorkspaceItemRequest,
  WorkspaceFileUrlResponse,
  UserPreferences,
  UserPreferencesData,
} from "@/types";
import { supabase } from "@/lib/supabase";

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

// -----------------------------------------------
// Token management (access token in MEMORY only)
// Refresh token is managed by Supabase via HttpOnly
// cookie through @supabase/ssr — never in localStorage.
// -----------------------------------------------

let accessToken: string | null = null;

export function setTokens(tokens: AuthTokens): void {
  accessToken = tokens.access_token;
  // Refresh token is NOT stored here — Supabase SSR handles it
  // via cookie-based session management automatically.
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

async function refreshAccessToken(): Promise<string> {
  // Deduplicate concurrent refresh attempts
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    // Use Supabase client to refresh the session.
    // @supabase/ssr manages the refresh token in an HttpOnly cookie,
    // so we don't need to pass it manually.
    const { data, error } = await supabase.auth.refreshSession();

    if (error || !data.session) {
      clearTokens();
      window.location.href = "/login";
      throw new Error("Token refresh failed");
    }

    accessToken = data.session.access_token;
    return data.session.access_token;
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
      window.location.href = "/login";
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
};

// -----------------------------------------------
// Auth API
// -----------------------------------------------

export const authApi = {
  login: (email: string, password: string) =>
    api.post<AuthResponse>("/auth/login", { email, password }),

  register: (data: {
    email: string;
    password: string;
    full_name_ar: string;
  }) => api.post<RegisterResponse>("/auth/register", data),

  refresh: () =>
    refreshAccessToken().then((token) => ({
      access_token: token,
      refresh_token: "", // Managed by Supabase SSR cookie
    })),

  logout: () => api.post<{ success: boolean }>("/auth/logout"),

  me: () => api.get<User>("/auth/me"),
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
  list: (params?: { case_id?: string | null; limit?: number; offset?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.case_id) searchParams.set("case_id", params.case_id);
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return api.get<ConversationListResponse>(`/conversations${qs ? `?${qs}` : ""}`);
  },

  get: (conversationId: string) =>
    api.get<{ conversation: ConversationDetail }>(`/conversations/${conversationId}`),

  create: (data: CreateConversationRequest) =>
    api.post<{ conversation: ConversationDetail }>("/conversations", data),

  update: (conversationId: string, title_ar: string) =>
    api.patch<{ conversation: ConversationDetail }>(`/conversations/${conversationId}`, { title_ar }),

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
        window.location.href = "/login";
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
    return api.get<DocumentListResponse>(`/cases/${caseId}/documents${qs ? `?${qs}` : ""}`);
  },

  upload: (caseId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.upload<Document>(`/cases/${caseId}/documents`, formData);
  },

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

  uploadAttachment: (conversationId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.upload<WorkspaceItem>(
      `/conversations/${conversationId}/workspace/attachments/upload`,
      formData,
    );
  },

  attachFromDocument: (
    conversationId: string,
    body: AttachFromDocumentRequest,
  ) =>
    api.post<WorkspaceItem>(
      `/conversations/${conversationId}/workspace/attachments/from-document`,
      body,
    ),
};

// -----------------------------------------------
// Preferences API
// -----------------------------------------------

export const preferencesApi = {
  get: () => api.get<UserPreferences>("/preferences"),

  update: (preferences: UserPreferencesData) =>
    api.patch<UserPreferences>("/preferences", { preferences }),
};
