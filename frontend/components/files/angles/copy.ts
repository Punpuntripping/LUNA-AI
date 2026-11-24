/**
 * Strings of the «الأوجه» tab. Wire labels (kinds, strength, sides, gaps,
 * confidence) live in `lib/contracts/angles.ts`. Every string here is a first
 * draft for the owner to approve in the internal preview.
 */
export const ANGLES_COPY = {
  /** The switch at the front of «الملفات» (owner 2026-11-24): «الخيوط» first, «الملفات» open by default.
   *  The user-facing name of an angle is «خيط» (owner 2026-11-24: "change الاوجه للخيوط"). */
  viewAngles: "الخيوط",
  viewFiles: "الملفات",
  viewSwitchLabel: "عرض الخيوط أو الملفات",
  /** Above the angles: what they are, and that they are analysis. */
  intro: "تحليل آلي يقترح الحجج التي تستحق البحث في الملفات التي أرسلتها معًا في رسالة واحدة — راجعه قبل الاعتماد عليه.",
  /** A map's heading: «رسالة أمس · ملفان · 8 خيوط». */
  message: (date: string) => (date ? `رسالة ${date}` : "رسالة"),
  noConversation: "محادثة",
  openMap: "عرض الخيوط",
  openConversation: "افتح المحادثة",
  backToFiles: "الملفات",
  loadError: "تعذّر تحميل الخيوط",
  retry: "إعادة المحاولة",
  mapGone: "هذه الخيوط لم تعد محدّثة — ربما تغيّر أحد ملفاتها.",
  libraryEmptyTitle: "لا توجد خيوط بعد",
  libraryEmptyHint:
    "تُستخرج الخيوط من الملفات التي ترسلها معًا في رسالة واحدة في المحادثة، بعد قراءتها وتقسيمها.",

  // Client
  client: "الموكّل",
  clientUnknown: "جانب الموكّل غير واضح من كلامك — الخيوط مقترحة لكل طرف",
  clientBasis: "كيف عُرف",
  inOtherDocuments: "صفته في المستندات الأخرى",

  // Map
  map: "الخريطة",
  mapHint: "كل سطر خيط يربط أقسامه بين المستندات. اضغط رقم القسم لقراءته، أو الخيط لتفاصيله.",
  mapHintSingle: "كل سطر خيط وأرقام أقسامه. اضغط رقم القسم لقراءته، أو الخيط لتفاصيله.",
  sectionLabel: (seq: number) => `القسم ${seq}`,
  showOnMap: "على الخريطة",
  scrollHint: "اسحب لرؤية بقية المستندات",

  // Cards
  angles: "الخيوط",
  crossDocument: "يربط بين المستندات",
  forParty: "لصالح",
  legalQuestion: "سؤال البحث",
  weakness: "ما يضعفه",
  sections: "الأقسام التي يستند إليها",
  ocrSuspect: "قد يكون خطأ قراءة",
  ocrSuspectHint: "يعتمد على رقم أو تاريخ قد يكون قُرئ خطأً من صورة الملف — تحقّق منه في الأصل",
  strength: "القوة",

  // Gaps
  gaps: "الفجوات",
  gapsHint: "تعارض بين المستندات، أو شيء ناقص، أو نقطة بلا رد",

  // Left out
  leftOut: "ملفات في الرسالة لم تدخل الخريطة",

  // Section dialog
  owner: "صاحبه",
  broughtBy: "قدّمه",
  pages: "الصفحات",
  anglesOfSection: "الخيوط التي تستند إليه",
  emptySection: "لا يحتوي هذا القسم على نص",
  close: "إغلاق",
} as const;
