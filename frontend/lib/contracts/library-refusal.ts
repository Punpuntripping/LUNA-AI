// The D14 refusal (HTTP 402 from `/library/full/*` and the workspace reference
// source reveal): its parser and its Arabic card copy. Moved from
// `lib/library/full-content.ts` (`parseRefusal`) and `lib/library/gate-copy.ts`
// (`refusalCardCopy`) — both web modules re-export / wrap these.
//
// ⚠ CTAs are a PARAMETER here, never baked in. The web points the upgrade CTA at
// its own pricing page; the mobile app must point it at the in-app purchase
// screen (or pass `null`) — it may never link to web checkout or web prices.

import { arNumber, arResetDate } from "./numerals";

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

// ------------------------------------------------------------------
// Refusal cards — one per `reason` on the D14 402 payload
// ------------------------------------------------------------------

export interface RefusalCardCopy {
  title: string;
  body: string;
  /** Omitted when there is nothing useful to click. */
  ctaLabel?: string;
  ctaHref?: string;
}

export interface RefusalCopyInput {
  reason: LibraryRefusalReason;
  resetsAt: string | null;
  /** Shelf size — only meaningful for `frozen_library` (§5B.4). */
  storedCount: number | null;
}

export interface RefusalCta {
  ctaLabel: string;
  ctaHref: string;
}

/**
 * Where the refusal card's button goes, per app. `null` = no button.
 * - `upgrade` — `quota_exhausted` / `frozen_library` / `locked`.
 * - `signup`  — `anonymous`.
 */
export interface RefusalCtas {
  upgrade: RefusalCta | null;
  signup: RefusalCta | null;
}

/**
 * The Arabic card for a refused reveal. Never blames the reader: an exhausted
 * period is a plan fact, an unresolvable item is our problem, and a frozen
 * shelf is framed as an asset the reader already owns (§5B.4 — the strongest
 * upgrade prompt in the product).
 */
export function refusalCardCopy(
  { reason, resetsAt, storedCount }: RefusalCopyInput,
  ctas: RefusalCtas,
): RefusalCardCopy {
  const date = arResetDate(resetsAt);
  const upgrade = ctas.upgrade ?? {};

  switch (reason) {
    case "quota_exhausted":
      return {
        title: "رصيد فتح المصادر لهذه الفترة انتهى",
        body: date
          ? `يتجدّد رصيدك في ${date}. يمكنك الترقية للوصول إلى عدد أكبر من المصادر الكاملة.`
          : "يمكنك الترقية للوصول إلى عدد أكبر من المصادر الكاملة.",
        ...upgrade,
      };

    case "frozen_library":
      return {
        title: "مصادر مكتبتك محفوظة",
        body:
          storedCount && storedCount > 0
            ? `لديك ${arNumber(storedCount)} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد.`
            : "هذا المصدر محفوظ في مكتبتك — رقِّ باقتك لفتحه من جديد.",
        ...upgrade,
      };

    case "anonymous":
      return {
        title: "سجّل مجاناً لعرض النص كاملاً",
        body: "حساب ريحان المجاني يفتح لك عشرة مصادر كاملة كل شهر.",
        ...(ctas.signup ?? {}),
      };

    case "locked":
      // Matches the UsageLimitsDialog activation notice, word for word.
      return {
        title: "حسابك غير مفعّل بعد",
        body: "تواصل معنا لتفعيل اشتراكك والبدء في استخدام ريحان.",
        ...upgrade,
      };

    case "unresolvable":
    default:
      // Never blame the reader, and never imply the item is missing.
      return {
        title: "تعذّر فتح هذا المصدر",
        body: "حاول مرة أخرى بعد قليل، أو افتح المصدر الرسمي أدناه.",
      };
  }
}
