import { create } from "zustand";
import type { User } from "@/types";
import { setTokens, clearTokens, getAccessToken, authApi, ApiClientError } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { usePreferencesStore } from "@/stores/preferences-store";

// Proactive refresh: refresh 5 minutes before token expiry
const REFRESH_BUFFER_MS = 5 * 60 * 1000;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

// Throttle focus-triggered revalidation so rapid tab focus/blur events
// do not hammer the auth server.
const REVALIDATE_THROTTLE_MS = 30 * 1000;
let lastRevalidateAt = 0;

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
  register: (
    email: string,
    password: string,
    full_name_ar: string,
    terms_version: string,
  ) => Promise<{ needsVerification: boolean }>;
  logout: () => Promise<void>;
  loadUser: () => Promise<void>;
  revalidateSession: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  error: null,

  setUser: (user) => set({ user, isAuthenticated: true }),

  clearError: () => set({ error: null }),

  login: async (email, password) => {
    set({ error: null });
    const response = await authApi.login(email, password);
    // Seed the browser supabase client so it writes the session cookie.
    // Without this, no `sb-<ref>-auth-token` cookie exists — and opening
    // a new tab finds nothing to hydrate from, signing the user out.
    // (OAuth login already seeds the cookie via exchangeCodeForSession in
    // app/auth/callback/route.ts; email/password used to skip this step.)
    await supabase.auth.setSession({
      access_token: response.access_token,
      refresh_token: response.refresh_token,
    });
    setTokens({
      access_token: response.access_token,
      refresh_token: response.refresh_token,
    });
    scheduleProactiveRefresh();
    set({ user: response.user, isAuthenticated: true });
  },

  register: async (email, password, full_name_ar, terms_version) => {
    set({ error: null });
    // Signup runs in the browser so the PKCE code_verifier cookie lives in
    // the same browser that will click the email-confirmation link. The
    // confirmation link redirects through /auth/callback, which calls
    // exchangeCodeForSession() and writes the session cookie.
    //
    // There is NO backend register route — signup is entirely client-side via
    // supabase.auth.signUp(). So the consent version (option B) is carried in
    // options.data.terms_version, which Supabase stores on the user's
    // raw_user_meta_data. Backend persistence (stamping terms_accepted_at +
    // terms_version onto the users row) must read it from there on bootstrap.
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { full_name_ar, terms_version },
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    });

    if (error) {
      const code = (error as { code?: string }).code ?? "";
      const msg = (error.message ?? "").toLowerCase();
      if (
        code === "user_already_exists" ||
        msg.includes("already") ||
        msg.includes("registered")
      ) {
        throw new ApiClientError(409, "validation_error", "البريد الإلكتروني مسجل مسبقاً");
      }
      if (code === "over_email_send_rate_limit" || msg.includes("rate limit")) {
        throw new ApiClientError(429, "rate_limited", "تم تجاوز الحد المسموح من الطلبات");
      }
      throw new ApiClientError(400, "validation_error", "فشل إنشاء الحساب");
    }

    // Supabase quirk: when "Confirm email" is on AND the address is already
    // registered, signUp returns a synthetic user with an empty identities
    // array (to avoid leaking which emails exist). Detect and surface as
    // duplicate so the user gets the same error as the explicit case.
    if (data.user && data.user.identities && data.user.identities.length === 0) {
      throw new ApiClientError(409, "validation_error", "البريد الإلكتروني مسجل مسبقاً");
    }

    // Email confirmation OFF → session issued immediately → log in now.
    if (data.session) {
      setTokens({
        access_token: data.session.access_token,
        refresh_token: data.session.refresh_token,
      });
      const user = await authApi.me();
      scheduleProactiveRefresh();
      set({ user, isAuthenticated: true });
      return { needsVerification: false };
    }

    // Email confirmation ON → no session yet; user must click the email link.
    return { needsVerification: true };
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
    // Drop cached user-scoped preferences so a next sign-in rehydrates from DB.
    usePreferencesStore.getState().reset();
    set({ user: null, isAuthenticated: false });
  },

  loadUser: async () => {
    try {
      set({ isLoading: true });

      // If access token is not in memory (e.g., page reload), restore the
      // session from the Supabase SSR cookie. We use refreshSession() rather
      // than getSession() so the access token is freshly minted and verified
      // against the auth server — a dead session is caught here, on page
      // load, instead of silently failing later on a message send.
      if (!getAccessToken()) {
        const { data, error } = await supabase.auth.refreshSession();
        let session = data?.session ?? null;

        // A refreshSession() error is NOT proof the session is dead. Refresh
        // tokens are single-use, so a concurrent refresh (another tab loading
        // at the same time, Supabase's own auto-refresh) may have already
        // rotated the token — this call then fails with "Already Used" while
        // the session is alive. Mirror revalidateSession() and confirm via
        // getSession() before tearing the session down.
        if (error || !session) {
          const { data: current } = await supabase.auth.getSession();
          session = current?.session ?? null;
        }

        if (!session) {
          // No valid session to restore — treat as signed out.
          clearTokens();
          set({ user: null, isAuthenticated: false, isLoading: false });
          return;
        }
        setTokens({
          access_token: session.access_token,
          refresh_token: session.refresh_token,
        });
      }

      const user = await authApi.me();
      lastRevalidateAt = Date.now();
      scheduleProactiveRefresh();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch {
      clearTokens();
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },

  revalidateSession: async () => {
    // Called when the browser tab regains focus. A backgrounded tab's
    // setTimeout-based proactive refresh is unreliable (timers are throttled
    // or frozen while the tab is hidden / the machine sleeps), so the access
    // token may have silently expired. Refresh now so a dead session is
    // surfaced immediately — before the user types and sends a message.
    if (!get().isAuthenticated) return;
    if (Date.now() - lastRevalidateAt < REVALIDATE_THROTTLE_MS) return;
    lastRevalidateAt = Date.now();

    try {
      const { data, error } = await supabase.auth.refreshSession();
      let session = data?.session ?? null;

      // A refreshSession() error is NOT proof the session is dead. Refresh
      // tokens are single-use, so a concurrent refresh (Supabase's own
      // auto-refresh, or the proactive-refresh timer) may have already
      // rotated the token — making this call fail with "Invalid Refresh
      // Token: Already Used" while the session is perfectly alive. Confirm
      // with getSession(), which reads the current session without forcing
      // another rotation, before tearing the user's session down.
      if (error || !session) {
        const { data: current } = await supabase.auth.getSession();
        session = current?.session ?? null;
      }

      if (!session) {
        // Genuinely no session — sign out locally so AuthGuard redirects.
        cancelProactiveRefresh();
        clearTokens();
        usePreferencesStore.getState().reset();
        set({ user: null, isAuthenticated: false });
        return;
      }
      setTokens({
        access_token: session.access_token,
        refresh_token: session.refresh_token,
      });
      scheduleProactiveRefresh();
    } catch {
      // Transient network error — keep the session as-is. The reactive 401
      // retry on the next request remains the fallback.
    }
  },
}));
