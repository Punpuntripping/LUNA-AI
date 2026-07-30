// Client-side helpers for the اسأل ريحان popup (SEO Library Phase 4).
//
// The two ANON endpoints (`POST /public/ask`, `GET /public/ask/{id}`) are called
// with a plain `fetch` — no auth header, no token client (they are deliberately
// anonymous). The AUTHED claim (`POST /ask/claim`) rides `api.claimAnonAnswer`
// in `lib/api.ts` (bearer + 401 retry) and is invoked from the AuthGuard intent
// consumer, not here.
//
// Server-side truncation is the trust boundary: an anon visitor only ever holds
// the teaser prefix; the full answer is revealed post-signup via claim. The
// localStorage keys below persist the anon session + the per-page question so a
// refresh re-shows the teaser without spending another question.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_PREFIX = "/api/v1";

// localStorage keys.
const SESSION_KEY = "rayhan_ask_session";
const QUESTION_KEY_PREFIX = "rayhan_ask_q";
// sessionStorage key for the post-signup claimed answer (the "continuity moment"
// bridge — written by the AuthGuard claim consumer, read-and-cleared here).
const CLAIMED_KEY = "rayhan_claimed_answer";

// ------------------------------------------------------------------
// Wire shapes (mirror backend `AnonAskResponse` / `AnonTeaserResponse`)
// ------------------------------------------------------------------

export interface AnonAskResult {
  question_id: string;
  session_key: string;
  visible_prefix: string;
  is_truncated: boolean;
  total_chars: number;
}

export interface AnonTeaser {
  question: string;
  visible_prefix: string;
  is_truncated: boolean;
  claimed: boolean;
}

export interface ClaimedAnswer {
  question: string;
  answer_md: string;
  page_type: string;
  page_id: string;
}

/** Classified failure of `POST /public/ask` so the panel can render the right copy. */
export type AskErrorKind = "disabled" | "rate_limited" | "forbidden" | "error";

export class AskError extends Error {
  constructor(
    public kind: AskErrorKind,
    message: string,
  ) {
    super(message);
    this.name = "AskError";
  }
}

export interface AnonAskInput {
  question: string;
  pageType: string;
  pageId: string;
  sessionKey: string | null;
  /**
   * Cloudflare Turnstile token, or `null` when the widget couldn't produce one
   * (no site key, script blocked, still solving). The backend skips
   * verification entirely while `TURNSTILE_SECRET_KEY` is unset and 403s on a
   * missing token once it is set — the caller never blocks on this, the server
   * decides. See `components/library/blocks/TurnstileGate.tsx`.
   */
  turnstileToken: string | null;
}

// ------------------------------------------------------------------
// Anon endpoints (plain fetch — no auth)
// ------------------------------------------------------------------

/**
 * Ask one anonymous question grounded in the current page. Throws an `AskError`
 * with a classified `kind` on the documented failure statuses:
 *   503 → disabled (kill switch / daily budget) · 429 → session cap reached ·
 *   403 → Turnstile failure · other non-OK → generic error.
 */
export async function postAnonAsk(input: AnonAskInput): Promise<AnonAskResult> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${API_PREFIX}/public/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: input.question,
        page_type: input.pageType,
        page_id: input.pageId,
        session_key: input.sessionKey,
        turnstile_token: input.turnstileToken,
      }),
    });
  } catch {
    throw new AskError("error", "تعذّر الاتصال، حاول مجدداً");
  }

  if (res.status === 503) {
    throw new AskError("disabled", "الخدمة غير متاحة حالياً");
  }
  if (res.status === 429) {
    throw new AskError(
      "rate_limited",
      "سؤالك المجاني مستخدم — سجّل لطرح المزيد",
    );
  }
  if (res.status === 403) {
    throw new AskError("forbidden", "فشل التحقق الأمني، حاول مجدداً");
  }
  if (!res.ok) {
    throw new AskError("error", "تعذّر معالجة سؤالك، حاول مجدداً");
  }
  return (await res.json()) as AnonAskResult;
}

/** Re-fetch one's own teaser after a refresh (id + session_key). `null` if gone. */
export async function getAnonTeaser(
  questionId: string,
  sessionKey: string,
): Promise<AnonTeaser | null> {
  try {
    const res = await fetch(
      `${API_BASE}${API_PREFIX}/public/ask/${encodeURIComponent(
        questionId,
      )}?session_key=${encodeURIComponent(sessionKey)}`,
    );
    if (!res.ok) return null;
    return (await res.json()) as AnonTeaser;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------
// localStorage — anon session + per-page question memory
// ------------------------------------------------------------------

export function getAskSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

export function setAskSession(sessionKey: string): void {
  try {
    localStorage.setItem(SESSION_KEY, sessionKey);
  } catch {
    // Storage unavailable (privacy mode) — the session simply isn't remembered.
  }
}

interface StoredQuestion {
  question_id: string;
  session_key: string;
}

function questionKey(pageType: string, pageId: string): string {
  return `${QUESTION_KEY_PREFIX}:${pageType}:${pageId}`;
}

export function getPageQuestion(
  pageType: string,
  pageId: string,
): StoredQuestion | null {
  try {
    const raw = localStorage.getItem(questionKey(pageType, pageId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredQuestion>;
    if (
      typeof parsed?.question_id !== "string" ||
      typeof parsed?.session_key !== "string"
    ) {
      return null;
    }
    return { question_id: parsed.question_id, session_key: parsed.session_key };
  } catch {
    return null;
  }
}

export function setPageQuestion(
  pageType: string,
  pageId: string,
  questionId: string,
  sessionKey: string,
): void {
  try {
    localStorage.setItem(
      questionKey(pageType, pageId),
      JSON.stringify({ question_id: questionId, session_key: sessionKey }),
    );
  } catch {
    // Best-effort — a refresh just won't re-show the teaser.
  }
}

// ------------------------------------------------------------------
// Claimed-answer bridge (sessionStorage) — the post-signup continuity moment
// ------------------------------------------------------------------

/** Written by the AuthGuard `claim_anon_answer` consumer after a successful claim. */
export function storeClaimedAnswer(answer: ClaimedAnswer): void {
  try {
    sessionStorage.setItem(CLAIMED_KEY, JSON.stringify(answer));
  } catch {
    // If we can't stash it, the widget just won't auto-reveal — harmless.
  }
}

/**
 * Read-and-clear a claimed answer IF it belongs to this page. The widget calls
 * this on mount: a match auto-opens the panel with the full answer exactly once
 * (cleared on read so a re-render / re-mount can't replay it).
 */
export function readClaimedAnswerForPage(
  pageType: string,
  pageId: string,
): ClaimedAnswer | null {
  try {
    const raw = sessionStorage.getItem(CLAIMED_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ClaimedAnswer>;
    if (parsed?.page_type !== pageType || parsed?.page_id !== pageId) {
      return null;
    }
    sessionStorage.removeItem(CLAIMED_KEY);
    if (
      typeof parsed.question !== "string" ||
      typeof parsed.answer_md !== "string"
    ) {
      return null;
    }
    return parsed as ClaimedAnswer;
  } catch {
    return null;
  }
}
