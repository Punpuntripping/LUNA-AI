"use client";

import { useEffect, useRef } from "react";

// Cloudflare Turnstile for the anonymous «اسأل ريحان» ask (Part 1.1 of the
// Cloudflare hardening plan).
//
// The backend is FAIL-CLOSED the moment `TURNSTILE_SECRET_KEY` is set:
// `ask_service.verify_turnstile` rejects a missing token and `public_ask.py`
// turns that into a 403. So the widget has to ship BEFORE the secret does,
// otherwise every anonymous ask 403s.
//
// The contract in the other direction matters just as much: while the secret is
// unset the backend skips verification entirely, so a missing or blocked widget
// must never block the UI. Every failure path here (no site key, script blocked
// by an extension or by CSP, widget error, stalled load) resolves to
// `unavailable`; the ask is then posted with a null token and the backend
// decides. The send button is deliberately never gated on the challenge.
//
// `managed` mode + `appearance: "interaction-only"` means the widget is
// invisible for a normal visitor and only paints when Cloudflare actually wants
// an interaction — no friction added to the happy path.

// Public site key — safe in the bundle (the secret lives only in the backend
// env). The literal fallback keeps local dev working with zero .env setup.
const SITE_KEY =
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || "0x4AAAAAAEAMNZfHSmuNL4Vm";

const SCRIPT_ID = "cf-turnstile-api";
const ONLOAD_CALLBACK = "__rayhanTurnstileReady";
const SCRIPT_SRC = `https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=${ONLOAD_CALLBACK}`;

// Third-party script: if it stalls we stop waiting and degrade rather than
// leaving the ask hanging on it.
const SCRIPT_TIMEOUT_MS = 8000;
// How long a submit waits for an in-flight challenge before posting without it.
const TOKEN_WAIT_MS = 4000;
const TOKEN_POLL_MS = 100;

// ------------------------------------------------------------------
// Turnstile client API (typed locally — no @types package is installed)
// ------------------------------------------------------------------

interface TurnstileRenderOptions {
  sitekey: string;
  action?: string;
  appearance?: "always" | "execute" | "interaction-only";
  size?: "normal" | "flexible" | "compact";
  theme?: "auto" | "light" | "dark";
  language?: string;
  "response-field"?: boolean;
  "refresh-expired"?: "auto" | "manual" | "never";
  callback?: (token: string) => void;
  "expired-callback"?: () => void;
  "timeout-callback"?: () => void;
  /** Return `true` to suppress Turnstile's own error UI — we degrade silently. */
  "error-callback"?: (code?: string) => boolean | void;
}

interface TurnstileApi {
  render: (el: HTMLElement, options: TurnstileRenderOptions) => string | undefined;
  reset: (widgetId?: string) => void;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    __rayhanTurnstileReady?: () => void;
  }
}

// ------------------------------------------------------------------
// Widget state — what the gate can currently offer a submit
// ------------------------------------------------------------------

export type TurnstileState =
  /** Script or challenge still in flight — a token may still arrive. */
  | { status: "pending"; token: null }
  /** Solved: a single-use token, ready to post. */
  | { status: "ready"; token: string }
  /** No site key, script blocked, or the widget errored — post a null token. */
  | { status: "unavailable"; token: null };

export const TURNSTILE_PENDING: TurnstileState = {
  status: "pending",
  token: null,
};

const TURNSTILE_UNAVAILABLE: TurnstileState = {
  status: "unavailable",
  token: null,
};

// ------------------------------------------------------------------
// Script loader — once per page, never throws
// ------------------------------------------------------------------

let scriptPromise: Promise<boolean> | null = null;

/**
 * Inject `api.js` once and resolve when `window.turnstile` is usable. Resolves
 * `false` (never rejects) on a network error, a CSP block or a stall, which is
 * what drives the `unavailable` degradation path.
 */
function loadTurnstileScript(): Promise<boolean> {
  if (typeof window === "undefined") return Promise.resolve(false);
  if (window.turnstile) return Promise.resolve(true);
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<boolean>((resolve) => {
    let settled = false;
    const done = (ok: boolean): void => {
      if (settled) return;
      settled = true;
      resolve(ok && Boolean(window.turnstile));
    };

    // `render=explicit` + `onload=` is the documented handshake — the API is
    // only guaranteed to be callable once Turnstile invokes this global.
    window.__rayhanTurnstileReady = () => done(true);

    const el = document.createElement("script");
    el.id = SCRIPT_ID;
    el.src = SCRIPT_SRC;
    el.async = true;
    el.defer = true;
    el.onerror = () => done(false);
    document.head.appendChild(el);

    window.setTimeout(() => done(false), SCRIPT_TIMEOUT_MS);
  });
  return scriptPromise;
}

// ------------------------------------------------------------------
// Token resolution
// ------------------------------------------------------------------

/**
 * The token to post, waiting up to `TOKEN_WAIT_MS` for a challenge that is
 * still solving (managed mode usually lands in well under a second). Returns
 * `null` — never throws, never blocks indefinitely — as soon as the gate says
 * it can't produce one, or when the grace period runs out.
 */
export async function resolveTurnstileToken(
  read: () => TurnstileState,
): Promise<string | null> {
  const deadline = Date.now() + TOKEN_WAIT_MS;
  for (;;) {
    const state = read();
    if (state.status === "ready") return state.token;
    if (state.status === "unavailable") return null;
    if (Date.now() >= deadline) return null;
    await new Promise((r) => window.setTimeout(r, TOKEN_POLL_MS));
  }
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

interface TurnstileGateProps {
  /**
   * Fired on every transition. The parent keeps the latest in a ref — nothing
   * should render from it, or the challenge becomes visible friction.
   */
  onStateChange: (state: TurnstileState) => void;
}

/**
 * Renders the (usually invisible) Turnstile challenge and reports its state.
 *
 * Tokens are SINGLE-USE: after a failed or spent submit, remount the gate by
 * changing its `key` to obtain a fresh one.
 */
export function TurnstileGate({ onStateChange }: TurnstileGateProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Latest callback without re-running the render effect (which would spawn a
  // second widget).
  const notifyRef = useRef(onStateChange);
  notifyRef.current = onStateChange;

  useEffect(() => {
    if (!SITE_KEY) {
      notifyRef.current(TURNSTILE_UNAVAILABLE);
      return;
    }

    let cancelled = false;
    let widgetId: string | undefined;

    void (async () => {
      const loaded = await loadTurnstileScript();
      if (cancelled) return;

      const api = window.turnstile;
      const el = containerRef.current;
      if (!loaded || !api || !el) {
        notifyRef.current(TURNSTILE_UNAVAILABLE);
        return;
      }

      try {
        widgetId = api.render(el, {
          sitekey: SITE_KEY,
          action: "anon_ask",
          appearance: "interaction-only",
          size: "flexible",
          theme: "auto",
          language: "ar",
          "response-field": false,
          "refresh-expired": "auto",
          callback: (token) =>
            notifyRef.current({ status: "ready", token }),
          "expired-callback": () => notifyRef.current(TURNSTILE_PENDING),
          "timeout-callback": () => notifyRef.current(TURNSTILE_PENDING),
          "error-callback": () => {
            notifyRef.current(TURNSTILE_UNAVAILABLE);
            return true; // Suppress the red error card — we degrade silently.
          },
        });
        if (!widgetId) notifyRef.current(TURNSTILE_UNAVAILABLE);
      } catch {
        notifyRef.current(TURNSTILE_UNAVAILABLE);
      }
    })();

    return () => {
      cancelled = true;
      if (!widgetId) return;
      try {
        window.turnstile?.remove(widgetId);
      } catch {
        // Already torn down by Turnstile itself — nothing to clean up.
      }
    };
  }, []);

  if (!SITE_KEY) return null;
  // Empty (zero-height) until Cloudflare decides a real interaction is needed.
  return <div ref={containerRef} className="flex justify-center empty:hidden" />;
}
