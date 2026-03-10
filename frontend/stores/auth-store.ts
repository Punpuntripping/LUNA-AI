import { create } from "zustand";
import type { User } from "@/types";
import { setTokens, clearTokens, authApi } from "@/lib/api";

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
    clearTokens();
    set({ user: null, isAuthenticated: false });
  },

  loadUser: async () => {
    try {
      set({ isLoading: true });
      const user = await authApi.me();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch {
      clearTokens();
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },
}));
