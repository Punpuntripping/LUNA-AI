// Client-side fetcher for the AUTHED, METERED full-content endpoint that powers
// the `FullContentGate` reveal (SEO Library — access tiers Phase B). The
// server-rendered page always ships the ANON, gate-truncated body (SEO-correct,
// no cloaking); a signed-in reader's DELIBERATE click on «اعرض النص كاملاً»
// fetches the full document here and swaps it in.
//
// ⚠ The charge sits on the REVEAL, not the page view (plan §5.1). Nothing in
// this module may be called from a mount effect — a signed-in user skimming ten
// judgment summaries must not burn ten unlocks.
//
//   GET /api/v1/library/full/{content_type}/{key}
//     200 → regulation → { sections: [{ id, title, text }] }
//           judgment   → { sections: [{ id, title, text }] }   (same shape)
//           article    → { text, sharh_md }
//           circular   → { text }
//           form       → { body_md }
//     402 → the D14 refusal body (NO content bytes):
//           { error: {code, message, status}, detail, reason, used, limit,
//             resets_at, stored_count? }
//           reason ∈ anonymous | quota_exhausted | frozen_library | locked
//                    | unresolvable
//           Anonymous is deliberately a 402, NOT a 401 — this endpoint is
//           reached from public pages and a 401 would trip the global
//           redirect-to-login and eject a browsing visitor.
//     404 → unknown key / unsupported type / unapproved form (never charged).
//
// The bearer comes from the in-memory access token (never localStorage — the XSS
// rule). A plain `fetch` is used (NOT the shared `apiFetch`) so a dead-session
// 401 can never trigger the global «redirect to /login» side effect on a public
// library page — the gate must be side-effect-free. The result is a DISCRIMINATED
// union so the caller can tell an exhausted quota (402 → show the card) from a
// dead session (401 → keep the anon render): the old `null`-on-any-failure
// contract made those two indistinguishable, which is the exact bug plan PART 5
// calls out.

import { getAccessToken } from "@/lib/api";
import type { UsageReport } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type FullContentType =
  | "regulation"
  | "judgment"
  | "article"
  | "circular"
  | "form";

export interface FullSection {
  id: string;
  /** Nullable on the wire — `LibraryFullSection.title` is `Optional[str]`. */
  title: string | null;
  text: string;
}

/** One «المصادر الرسمية» entry. Matches the backend's `OfficialSource`. */
export interface FullOfficialSource {
  title: string;
  href: string;
}

/**
 * Carried by EVERY reveal payload.
 *
 * User decision 2026-07-28: «المصادر الرسمية» is part of what an unlock buys,
 * reversing the plan's §1.2 "always shown, gated or not". A gated document's
 * ANON payload now returns `official_sources: []`, so the page-level
 * `<OfficialSources>` renders nothing (it returns null on an empty list) and
 * this is the only path by which the block reaches a reader.
 *
 * Empty for `article` and `form`: a مادة page never had its own (its parent نظام
 * carries them) and `FormDetail` has no such field.
 */
interface WithOfficialSources {
  official_sources?: FullOfficialSource[];
}

/**
 * The `kind="sections"` payload. Shared VERBATIM by regulations and judgments —
 * a judgment's authed reveal is the same `{ sections: [{ id, title, text }] }`
 * envelope (its ids are the section keys `facts`/`ruling`/… instead of chunk
 * uuids), so `FullContentGate` needs no judgment-specific branch.
 */
export interface FullRegulation extends WithOfficialSources {
  sections: FullSection[];
}

export interface FullArticle extends WithOfficialSources {
  text: string;
  /**
   * Nullable on the wire — `get_full_article` returns `None` when the مادة has
   * no شرح row. Typed honestly so nobody writes `sharh_md.length` and gets a
   * runtime error past a clean `tsc`.
   */
  sharh_md: string | null;
}

export interface FullCircular extends WithOfficialSources {
  text: string;
}

export interface FullForm extends WithOfficialSources {
  body_md: string;
}

export type FullContentPayload =
  | FullRegulation
  | FullArticle
  | FullCircular
  | FullForm;

// ------------------------------------------------------------------
// Refusal (D14) — the 402 contract
// ------------------------------------------------------------------

export type LibraryRefusalReason =
  | "anonymous"
  | "quota_exhausted"
  | "frozen_library"
  | "locked"
  | "unresolvable";

const REFUSAL_REASONS: readonly string[] = [
  "anonymous",
  "quota_exhausted",
  "frozen_library",
  "locked",
  "unresolvable",
];

/** The normalized 402 body. `stored_count` is only sent for `frozen_library`. */
export interface LibraryRefusal {
  reason: LibraryRefusalReason;
  /** The backend's own Arabic message — a last-resort fallback for the card. */
  message: string;
  used: number;
  /** `null` = unlimited (so a refusal with a null limit should never happen). */
  limit: number | null;
  resets_at: string | null;
  stored_count: number | null;
}

/** Why a reveal produced no content, when it was NOT an entitlement refusal. */
export type FullContentError =
  /** No in-memory access token — the reader is (or has become) anonymous. */
  | "no_token"
  /** 401/403: the session died. NEVER redirect — this is a public page. */
  | "unauthorized"
  /** 404: unknown key, unsupported type, or an unapproved form. */
  | "not_found"
  /**
   * 429: the shared 20/min reveal budget. `/library/full` and the workspace
   * reference-source endpoint draw on ONE bucket per verified caller (D13.2), so
   * opening a couple of reference dialogs and then hitting a reveal reaches this
   * realistically. It is NOT a quota refusal and NOT a network fault — nothing
   * was charged, and saying so is the whole point of its copy.
   */
  | "rate_limited"
  /** Network failure / unparsable body. */
  | "network"
  /** 5xx or any other unexpected status. */
  | "server";

/**
 * The reveal outcome. Narrow on `ok`, then on `kind`:
 *   { ok: true,  data }                         → swap in the full content
 *   { ok: false, kind: "refusal", refusal }     → render the D14 refusal card
 *   { ok: false, kind: "error", error, status } → keep the anon render
 */
export type FullContentResult<T extends FullContentPayload> =
  | { ok: true; data: T }
  | { ok: false; kind: "refusal"; refusal: LibraryRefusal }
  | { ok: false; kind: "error"; error: FullContentError; status: number | null };

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function asIntOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.trunc(value)
    : null;
}

/**
 * Normalize a 402 body defensively. A refusal must ALWAYS produce a renderable
 * card, so every field falls back rather than throwing — an unrecognised
 * `reason` degrades to `unresolvable`, whose copy never blames the reader.
 *
 * Exported (Phase C) so the workspace reference-source reveal parses the SAME
 * D14 body with the SAME defensiveness. There is exactly one refusal shape on
 * the wire; there must be exactly one parser for it, or the two surfaces drift.
 */
export function parseRefusal(body: unknown): LibraryRefusal {
  const raw = asRecord(body);
  const error = asRecord(raw.error);

  const rawReason = typeof raw.reason === "string" ? raw.reason : "";
  const reason: LibraryRefusalReason = REFUSAL_REASONS.includes(rawReason)
    ? (rawReason as LibraryRefusalReason)
    : "unresolvable";

  const message =
    (typeof error.message === "string" && error.message) ||
    (typeof raw.detail === "string" && raw.detail) ||
    "";

  return {
    reason,
    message,
    used: asIntOrNull(raw.used) ?? 0,
    limit: asIntOrNull(raw.limit),
    resets_at: typeof raw.resets_at === "string" ? raw.resets_at : null,
    stored_count: asIntOrNull(raw.stored_count),
  };
}

/**
 * Encode an Arabic key for the backend path exactly once (Next delivers non-ASCII
 * params already percent-encoded — decode first so we never double-encode).
 */
function encodeKey(key: string): string {
  let decoded = key;
  try {
    decoded = decodeURIComponent(key);
  } catch {
    decoded = key;
  }
  // Per-SEGMENT encoding: the article key is "{reg_slug}/{article_slug}" and the
  // backend splits on the literal '/' between the two percent-encoded segments —
  // encoding the whole key would turn it into '%2F' and break the split.
  return decoded.split("/").map(encodeURIComponent).join("/");
}

/**
 * Spend one unlock and fetch the full document. CALL ONLY FROM A USER GESTURE.
 *
 * Re-visits are free by construction (`ON CONFLICT DO NOTHING` server-side), so
 * a reveal on an already-unlocked item just succeeds — do NOT cache entitlement
 * client-side, and never try to predict the answer.
 */
export async function fetchFullContent<T extends FullContentPayload>(
  contentType: FullContentType,
  key: string,
): Promise<FullContentResult<T>> {
  const token = getAccessToken();
  if (!token) {
    return { ok: false, kind: "error", error: "no_token", status: null };
  }

  let res: Response;
  try {
    res = await fetch(
      `${API_BASE}/api/v1/library/full/${contentType}/${encodeKey(key)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
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
    const error: FullContentError =
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
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, kind: "error", error: "network", status: res.status };
  }
}

// ------------------------------------------------------------------
// Passive balance — «no prompt, but never a silent meter» (§5.1)
// ------------------------------------------------------------------

/** The «فتح المصادر» allowance, projected for the chip beside the reveal action. */
export interface LibraryBalance {
  used: number;
  /** `null` = unlimited. */
  limit: number | null;
  /** `null` = unlimited; otherwise `max(limit - used, 0)`. */
  remaining: number | null;
  resets_at: string | null;
}

/**
 * Read the caller's library allowance off `/api/v1/usage`.
 *
 * Plain `fetch` again, and for the same reason: this runs on PUBLIC library
 * pages, where the shared `apiFetch`'s 401 → redirect-to-login would eject a
 * reader whose session merely expired in a background tab. Returns `null` for
 * anonymous readers, locked accounts (`library.period === null`) and every
 * failure — the chip simply does not render.
 *
 * Reading the balance costs nothing and charges nothing, so — unlike
 * `fetchFullContent` — this one IS safe to call on mount.
 */
export async function fetchLibraryBalance(): Promise<LibraryBalance | null> {
  const token = getAccessToken();
  if (!token) return null;

  try {
    const res = await fetch(`${API_BASE}/api/v1/usage`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    const report = (await res.json()) as UsageReport;
    const bar = report?.library?.period ?? null;
    if (!bar) return null;
    const limit = typeof bar.limit === "number" ? bar.limit : null;
    const used = typeof bar.used === "number" ? bar.used : 0;
    return {
      used,
      limit,
      remaining: limit === null ? null : Math.max(limit - used, 0),
      resets_at: bar.resets_at ?? null,
    };
  } catch {
    return null;
  }
}
