/**
 * Strings of the «الأوجه» tab. Wire labels (kinds, strength, sides, gaps,
 * confidence) live in `lib/contracts/angles.ts`. Every string here is a first
 * draft for the owner to approve in the internal preview.
 */
export const ANGLES_COPY = {
  tab: "الأوجه",
  sectionsTab: "الأقسام",
  /** Under the tab title: what the map is, and that it is analysis. */
  intro: "تحليل آلي يقترح الحجج التي تستحق البحث في ملفات هذه الرسالة — راجعه قبل الاعتماد عليه.",
  fromConversation: "من محادثة",

  // Client
  client: "الموكّل",
  clientUnknown: "جانب الموكّل غير واضح من كلامك — الأوجه مقترحة لكل طرف",
  clientBasis: "كيف عُرف",
  inOtherDocuments: "صفته في المستندات الأخرى",

  // Map
  map: "الخريطة",
  mapHint: "كل سطر وجه، وخطه يربط أقسامه بين المستندات. اضغط رقم القسم لقراءته، أو الوجه لتفاصيله.",
  mapHintSingle: "كل سطر وجه وأرقام أقسامه. اضغط رقم القسم لقراءته، أو الوجه لتفاصيله.",
  thisFile: "هذا الملف",
  sectionLabel: (seq: number) => `القسم ${seq}`,
  showOnMap: "على الخريطة",
  scrollHint: "اسحب لرؤية بقية المستندات",

  // Cards
  angles: "الأوجه",
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
  anglesOfSection: "الأوجه التي تستند إليه",
  emptySection: "لا يحتوي هذا القسم على نص",
  close: "إغلاق",
} as const;
