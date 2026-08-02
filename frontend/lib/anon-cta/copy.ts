// EVERY Arabic string the anon conversion popup renders lives HERE, in ONE file
// — the same discipline `lib/library/gate-copy.ts` enforces for the gate.
// `components/marketing/AnonCtaPopup.tsx` may not hardcode a single word.
//
// FRAMING RULE (plan §1): the popup is the softest surface in the product. It
// interrupts a reader who is not being refused anything, so: no urgency, no
// countdown, no fake scarcity, no «you have read 3 of 5 free articles». One
// sentence of value, two buttons, an obvious X.
//
// The wording is `BlogConversionCta`'s pitch VERBATIM, and that is the point:
// one pitch, one wording, so a future rewrite touches one string table instead
// of hunting the same sentence across a card and a modal.

export const anonCtaCopy = {
  /** The dialog title — also its accessible name (Radix `DialogTitle`). */
  title: "جرّب ريحان مجاناً",
  /** One sentence of value. Nothing about limits, quotas, or what is locked. */
  body: "المساعد القانوني الذكي للمحامين السعوديين — أنشئ تحليلاتك القانونية ومذكراتك مدعومة بالأنظمة والسوابق.",
  /** Primary action → /login?next=…&mode=register (it promises signup, §7.7). */
  primaryCta: "ابدأ الآن",
  /** Secondary action → /login?next=… for a reader who already has an account. */
  secondaryCta: "تسجيل الدخول",
} as const;
