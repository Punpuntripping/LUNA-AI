import { create } from "zustand";

import { LEGAL_VERSION, type AuthResponse, type SignupResponse, type User } from "@contracts";

import * as authApi from "@/api/auth";
import { ApiError, errorMessage, MSG_UNKNOWN } from "@/api/errors";
import { queryClient } from "@/lib/query-client";

import {
  cancelProactiveRefresh,
  isAccessTokenStale,
  refreshAccessToken,
  scheduleProactiveRefresh,
  SessionExpiredError,
  setSessionExpiredHandler,
} from "./refresh";
import { clearTokens, getAccessToken, getRefreshToken, setAccessToken, setRefreshToken, wipeIfFreshInstall } from "./token-store";

/**
 * - `booting`      cold start in progress (splash stays up)
 * - `signedOut`    no session → (auth) screens
 * - `signedIn`     session + profile → (app) screens
 * - `unavailable`  a refresh token exists but the server could not be reached
 *                  (offline / 503). The session is KEPT; the user can retry.
 */
export type SessionStatus = "booting" | "signedOut" | "signedIn" | "unavailable";

export interface SignupInput {
  email: string;
  password: string;
  fullName: string;
  marketingOptIn: boolean;
}

interface SessionState {
  status: SessionStatus;
  user: User | null;
  /** Arabic reason shown on the `unavailable` screen. */
  unavailableMessage: string | null;

  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  loginWithOtp: (email: string, code: string) => Promise<void>;
  /** Resolves to "verify" when an emailed code must be confirmed next. */
  signup: (input: SignupInput) => Promise<"verify" | "signedIn">;
  verifySignup: (email: string, code: string) => Promise<void>;
  /** Emailed reset code + new password → signed in. */
  resetPassword: (email: string, code: string, newPassword: string) => Promise<void>;
  refreshUser: () => Promise<void>;
  restoreAccount: () => Promise<void>;
  /** Called when the app returns to the foreground. */
  resume: () => Promise<void>;
  logout: () => Promise<void>;
}

const normaliseEmail = (email: string) => email.trim().toLowerCase();

export const useSession = create<SessionState>((set, get) => {
  /** The one post-login path for every grant (password, code, signup, reset). */
  async function establishSession(res: AuthResponse): Promise<void> {
    // Persist the refresh token FIRST — see refresh.ts.
    await setRefreshToken(res.refresh_token);
    setAccessToken(res.access_token);
    scheduleProactiveRefresh(res.access_token);
    set({ status: "signedIn", user: res.user, unavailableMessage: null });
    // The login payload omits plan/paid fields — fill them from /me.
    get().refreshUser().catch(() => undefined);
  }

  function signedOut(): void {
    queryClient.clear();
    set({ status: "signedOut", user: null, unavailableMessage: null });
  }

  setSessionExpiredHandler(signedOut);

  return {
    status: "booting",
    user: null,
    unavailableMessage: null,

    bootstrap: async () => {
      await wipeIfFreshInstall();
      if (!(await getRefreshToken())) {
        set({ status: "signedOut" });
        return;
      }
      try {
        await refreshAccessToken();
        const user = await authApi.fetchMe();
        set({ status: "signedIn", user, unavailableMessage: null });
      } catch (err) {
        if (err instanceof SessionExpiredError) {
          signedOut();
          return;
        }
        if (err instanceof ApiError && err.kind === "http" && err.status === 401) {
          signedOut();
          return;
        }
        set({ status: "unavailable", unavailableMessage: errorMessage(err, MSG_UNKNOWN) });
      }
    },

    login: async (email, password) => {
      await establishSession(await authApi.login({ email: normaliseEmail(email), password }));
    },

    loginWithOtp: async (email, code) => {
      await establishSession(await authApi.verifyOtp({ email: normaliseEmail(email), code }));
    },

    signup: async ({ email, password, fullName, marketingOptIn }) => {
      const res: SignupResponse = await authApi.signup({
        email: normaliseEmail(email),
        password,
        full_name: fullName.trim(),
        terms_version: LEGAL_VERSION,
        marketing_opt_in: marketingOptIn,
      });
      if (authApi.isAuthResponse(res)) {
        await establishSession(res);
        return "signedIn";
      }
      return "verify";
    },

    verifySignup: async (email, code) => {
      await establishSession(await authApi.verifySignup({ email: normaliseEmail(email), code }));
    },

    resetPassword: async (email, code, newPassword) => {
      await establishSession(
        await authApi.resetPassword({ email: normaliseEmail(email), code, new_password: newPassword }),
      );
    },

    refreshUser: async () => {
      const user = await authApi.fetchMe();
      set({ user });
    },

    restoreAccount: async () => {
      await authApi.restoreAccount();
      await get().refreshUser();
    },

    resume: async () => {
      const { status } = get();
      if (status === "unavailable") {
        await get().bootstrap();
        return;
      }
      if (status === "signedIn" && isAccessTokenStale(getAccessToken())) {
        // Outage keeps the session; expiry is routed by the handler.
        await refreshAccessToken().catch(() => undefined);
      }
    },

    logout: async () => {
      const token = getAccessToken();
      cancelProactiveRefresh();
      if (token) {
        // Always 200 server-side; a failure here must not keep anyone signed in.
        await authApi.logout(token).catch(() => undefined);
      }
      await clearTokens();
      signedOut();
    },
  };
});
