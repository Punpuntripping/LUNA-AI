/**
 * Every Arabic string the «الملفات» screens render that is not a wire label
 * (type names, card states and the approval question live in
 * `lib/contracts/files.ts`, shared with the mobile app).
 *
 * The section is NAMED «الملفات» (owner, 2026-11-19 — the plan's «ملفاتي» is
 * retired); natural phrases about the user's own files («ملفاتك») stay.
 * Strings quoted from `.claude/plans/my_files.md` or the owner are verbatim;
 * the others are first drafts for the owner to approve in the preview.
 */
export const FILES_COPY = {
  // Sidebar / page
  sidebarConversations: "المحادثات",
  sidebarFiles: "الملفات",
  pageTitle: "الملفات",
  /** Subtitle before the first file arrives (with files it is the count). */
  pageSubtitleEmpty: "الملفات التي ترسلها في محادثاتك تُجمع هنا.",
  /** «120 ملفًا من محادثاتك» — the count comes from `formatFileCount`. */
  pageSubtitleSuffix: "من محادثاتك",

  // Pane
  paneTitle: "المخرجات",
  paneItemsTab: "العناصر",
  paneFilesTab: "الملفات",
  paneFilesEmpty: "لا توجد ملفات بعد",
  addItem: "إضافة عنصر",
  addFile: "إضافة ملف",
  fromLibrary: "من الملفات",

  // Library list
  allChip: "الكل",
  typeChipsLabel: "تصفية حسب نوع المستند",
  loadError: "تعذّر تحميل ملفاتك",
  retry: "إعادة المحاولة",
  emptyTitle: "لا توجد ملفات بعد",
  emptyHint:
    "الملفات التي ترسلها في المحادثات تُجمع هنا تلقائيًا بعد استخراج نصها، مرتبةً حسب نوع المستند.",
  upload: "رفع ملف",
  soon: "قريبًا",

  // Card
  cardMenu: "خيارات الملف",
  open: "فتح",
  addToConversation: "أضف إلى محادثة",
  removeFromLibrary: "حذف من الملفات",
  noSummary: "لا يوجد ملخص بعد",
  /** Owner, 2026-11-20: advise re-attaching, with no reason given. */
  reattachAdvised: "ننصح بإعادة إرفاق هذا الملف",
  /** The quiet marker on a file that opens as an outline (`has_sections`). */
  hasSections: "مقسّم إلى أقسام",

  // Delete confirmation (§3.7)
  deleteTitle: "حذف من الملفات",
  deleteBody:
    "سيُحذف هذا الملف من «الملفات» ولن يظهر عند إرفاق الملفات بعد الآن، ويبقى كما هو داخل المحادثات التي استُخدم فيها.",
  deleteConfirm: "حذف من الملفات",
  cancel: "إلغاء",
  deleteError: "تعذّر حذف الملف، حاول مرة أخرى",

  // Viewer (§3.3)
  original: "الملف الأصلي",
  extractedText: "النص المستخرج",
  copyText: "نسخ النص",
  purged: "حُذف الملف الأصلي بعد انتهاء مدة الاحتفاظ",
  summary: "الملخص",
  sections: "الأقسام",
  expandAll: "توسيع الكل",
  collapseAll: "طيّ الكل",
  metadataTag: "بيانات المستند",
  /** seg-v5.2 section owner: the filter above the sections and «قدّمه: …» on a row. */
  ownerFilterLabel: "تصفية حسب صاحب القسم",
  broughtBy: "قدّمه",
  emptySection: "لا يحتوي هذا القسم على نص",
  noText: "لا يوجد نص مستخرج لهذا الملف",
  backToLibrary: "الملفات",
  fileNotFound: "لم نجد هذا الملف في ملفاتك",
  fileLoadError: "تعذّر تحميل الملف",
} as const;
