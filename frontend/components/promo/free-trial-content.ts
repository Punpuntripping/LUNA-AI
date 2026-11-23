/**
 * Single source for the free-week copy (migration 205). The week is offered as
 * the Basic card at 0, so the name, tagline and struck list price come from the
 * catalog. The limits below are the granted plan's, which differ from Basic's,
 * so Basic's feature list is deliberately not reused.
 *
 * Two plans, told apart by length (the server sends `days`, see
 * `free_trial_days()`):
 *   - 3 days → `marketing_lawyer_3` (migration 232): 10 / session, 50 / week, 25 pages
 *   - 7 days → `marketing_lawyer_7` (migration 232): 15 / session, 74 / week, 50 pages
 * 14 days (`marketing_lawyer_14`, the 232b rollback) keeps the 7-day limits.
 * Before 232b, 7 days meant `marketing_lawyer_v2` (10 / 50 / 25), so 232b must be
 * applied right after this copy goes live.
 * Keep `freeTrialLength` in step with those rows. An unknown length falls back to
 * 3 days, the smaller limits, so the card can never promise more than the claim
 * grants.
 */

/** The catalog card the week is presented as. */
export const FREE_TRIAL_PLAN_ID = "basic" as const;

/**
 * `plans.ocr_pages_monthly` for `free`, and (`freeTrialOcrPages`) for the granted
 * plan. Used only to decide when to show the offer, so a drift here mistimes a
 * popup and nothing more: the quota gate reads the database on every send.
 */
export const FREE_OCR_PAGES = 3;

export function freeTrialOcrPages(days: number): number {
  return days >= 7 ? 50 : 25;
}

/** The server sets the length per account. This is only the fallback. */
export const FREE_TRIAL_DEFAULT_DAYS = 3;

/** «لمدة …» and the badge for a length. Only 3, 7 and 14 are ever served. */
function trialSpan(days: number): { span: string; badge: string } {
  if (days >= 14) return { span: "أسبوعين", badge: "أسبوعان مجاناً" };
  if (days >= 7) return { span: "أسبوع", badge: "أسبوع مجاني" };
  return { span: `${days} أيام`, badge: `${days} أيام مجاناً` };
}

/** The wording, and the limits, that change with the trial's length. */
export function freeTrialLength(days: number) {
  const full = days >= 7; // the 15 / 74 / 50 plans
  const { span, badge } = trialSpan(days);
  return {
    title: `جرّب ريحان مجاناً لمدة ${span}`,
    badge,
    period: `لمدة ${span}`,
    /** The card's limit lines, in display order. */
    limits: [
      days >= 14
        ? "74 نقطة استخدام أسبوعياً"
        : `${full ? 74 : 50} نقطة استخدام طوال مدة العرض`,
      full ? "15 نقطة لكل جلسة (5 ساعات)" : "10 نقاط لكل جلسة (5 ساعات)",
      `${freeTrialOcrPages(days)} صفحة استخراج نص`,
    ],
  };
}

export const FREE_TRIAL_COPY = {
  price: "0",
  note: "بدون بطاقة، ولا يُخصم منك شيء",
  cta: "احصل على العرض",
  pending: "جارٍ التفعيل…",
  /** The dialog's banner line: true because the blocked message sends itself. */
  unblock: "فعّل العرض وسنكمل طلبك فوراً.",
  /** Nothing was refused yet, so nothing resumes: the file stays attached. */
  unblockPdf: "فعّل العرض ويبقى ملفك مرفقاً لترسله.",
  pdfLead: (pages: number): string =>
    `عدد صفحات ملفك ${pages}، والباقة المجانية تشمل ${FREE_OCR_PAGES} صفحات.`,
  failed: "تعذّر تفعيل العرض. حاول مرة أخرى.",
  /** Shown after `freeTrialLength(days).limits`; the same on both lengths. */
  features: [
    "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
    "الوصول الكامل للمكتبة القانونية",
  ],
} as const;
