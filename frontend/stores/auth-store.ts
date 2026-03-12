import { create } from "zustand";
import type { User } from "@/types";
import { setTokens, clearTokens, getAccessToken, authApi } from "@/lib/api";
import { supabase } from "@/lib/supabase";

// Proactive refresh: refresh 5 minutes before token expiry
const REFRESH_BUFFER_MS = 5 * 60 * 1000;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

function decodeTokenExp(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp ? payload.exp * 1000 : null; // Convert to ms
  } catch {
    return null;
  }
}

function scheduleProactiveRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer);
  const token = getAccessToken();
  if (!token) return;

  const expMs = decodeTokenExp(token);
  if (!expMs) return;

  const refreshAt = expMs - REFRESH_BUFFER_MS - Date.now();
  if (refreshAt <= 0) return; // Already past refresh window

  refreshTimer = setTimeout(async () => {
    try {
      const { data, error } = await supabase.auth.refreshSession();
      if (!error && data.session) {
        setTokens({
          access_token: data.session.access_token,
          refresh_token: data.session.refresh_token,
        });
        scheduleProactiveRefresh(); // Schedule next refresh
      }
    } catch {
      // Fail silently — reactive 401 retry is the fallback
    }
  }, refreshAt);
}

function cancelProactiveRefresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;

  setUser: (user: User) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, full_name_ar: string) => Promise<void>;
  logout: () => Promise<void>;
  loadUser: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  error: null,

  setUser: (user) => set({ user, isAuthenticated: true }),

  clearError: () => set({ error: null }),

  login: async (email, password) => {
    set({ error: null });
    const response = await authApi.login(email, password);
    setTokens({
      access_token: response.access_token,
      refresh_token: response.refresh_token,
    });
    scheduleProactiveRefresh();
    set({ user: response.user, isAuthenticated: true });
  },

  register: async (email, password, full_name_ar) => {
    set({ error: null });
    await authApi.register({ email, password, full_name_ar });
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore errors on logout
    }
    cancelProactiveRefresh();
    clearTokens();
    await supabase.auth.signOut();
    set({ user: null, isAuthenticated: false });
  },

  loadUser: async () => {
    try {
      set({ isLoading: true });

      // If access token is not in memory (e.g., page reload),
      // try to restore it from Supabase SSR session (cookie-based).
      if (!getAccessToken()) {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.access_token) {
          setTokens({
            access_token: session.access_token,
            refresh_token: session.refresh_token,
          });
        } else {
          // No session to restore
          set({ user: null, isAuthenticated: false, isLoading: false });
          return;
        }
      }

      const user = await authApi.me();
      scheduleProactiveRefresh();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch {
      clearTokens();
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },
}));
