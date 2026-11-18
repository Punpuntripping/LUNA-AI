/**
 * Single source for the free-week copy (migration 205). The week is offered as
 * the Basic card at 0, so the name, tagline and struck list price come from the
 * catalog. The limits below are the granted plan's (`marketing_lawyer_v2` /
 * `marketing_lawyer_14_v2`, migration 219: 10 per session, 50 a week, 25 pages),
 * which differ from Basic's, so Basic's feature list is deliberately not reused.
 * Trials claimed before 219b keep the old rows (15 / 74 / 50); this copy is only
 * ever shown before a claim, so it describes the new rows alone.
 */

/** The catalog card the week is presented as. */
export const FREE_TRIAL_PLAN_ID = "basic" as const;

/**
 * `plans.ocr_pages_monthly` for `free` and for the granted plan. Used only to
 * decide when to show the offer, so a drift here mistimes a popup and nothing
 * more: the quota gate reads the database on every send.
 */
export const FREE_OCR_PAGES = 3;
export const FREE_TRIAL_OCR_PAGES = 25;

/** The server sets the length per account. This is only the fallback. */
export const FREE_TRIAL_DEFAULT_DAYS = 7;

/** The wording that changes with the trial's length. */
export function freeTrialLength(days: number) {
  const twoWeeks = days >= 14;
  const span = twoWeeks ? "أسبوعين" : "أسبوع";
  return {
    title: `جرّب ريحان مجاناً لمدة ${span}`,
    badge: twoWeeks ? "أسبوعان مجاناً" : "أسبوع مجاني",
    period: `لمدة ${span}`,
    points: twoWeeks ? "50 نقطة استخدام أسبوعياً" : "50 نقطة استخدام طوال الأسبوع",
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
  features: [
    "10 نقاط لكل جلسة (5 ساعات)",
    `${FREE_TRIAL_OCR_PAGES} صفحة استخراج نص`,
    "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
    "الوصول الكامل للمكتبة القانونية",
  ],
} as const;
