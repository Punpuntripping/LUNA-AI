import type {
  AuthResponse,
  AuthTokens,
  User,
  RegisterResponse,
} from "@/types";

interface ApiError {
  detail: string;
  code: string;
  status: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_PREFIX = "/api/v1";

// -----------------------------------------------
// Token management (stored in memory + localStorage)
// -----------------------------------------------

let accessToken: string | null = null;

export function setTokens(tokens: AuthTokens): void {
  accessToken = tokens.access_token;
  localStorage.setItem("refresh_token", tokens.refresh_token);
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function clearTokens(): void {
  accessToken = null;
  localStorage.removeItem("refresh_token");
}

// -----------------------------------------------
// Refresh logic
// -----------------------------------------------

let refreshPromise: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  // Deduplicate concurrent refresh attempts
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    const refreshToken = localStorage.getItem("refresh_token");
    if (!refreshToken) throw new Error("No refresh token");

    const res = await fetch(`${API_BASE}${API_PREFIX}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) {
      clearTokens();
      window.location.href = "/login";
      throw new Error("Token refresh failed");
    }

    const data: AuthTokens = await res.json();
    setTokens(data);
    return data.access_token;
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

  // Handle 401: attempt token refresh once
  if (res.status === 401 && retry) {
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
    let errorBody: ApiError;
    try {
      errorBody = await res.json();
    } catch {
      errorBody = {
        detail: res.statusText,
        code: "unknown",
        status: res.status,
      };
    }
    throw new ApiClientError(
      res.status,
      errorBody.code || "unknown",
      errorBody.detail || "Request failed"
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

  refresh: (refreshToken: string) =>
    api.post<AuthTokens>("/auth/refresh", { refresh_token: refreshToken }),

  logout: () => api.post<{ success: boolean }>("/auth/logout"),

  me: () => api.get<User>("/auth/me"),
};
