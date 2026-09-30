/**
 * Single source of truth for the public landing page (`/`) copy + numbers.
 *
 * Keep every marketing claim and headline string here so the page stays easy
 * to tweak in one place. The corpus counts below are the live database floors
 * (regulations_v2 / cases / circulars) as of 2026-10-01 (3,956 / 30,531 / 1,843) — phrased as "أكثر من"
 * so they stay honest as the corpus grows. Round them up here if you ever
 * want bolder numbers; nothing downstream hard-codes them.
 */
import {
  Scale,
  Gavel,
  Building2,
  PenLine,
  ShieldCheck,
  Library,
  ScanText,
  FileUp,
  Search,
  ClipboardCopy,
  type LucideIcon,
} from "lucide-react";

/** Where the primary "ابدأ الآن" CTA sends prospects. Signup lives on /login. */
export const PRIMARY_CTA_HREF = "/login";

/** Support inbox used for early-access / activation-code requests. */
export const SUPPORT_EMAIL = "support@rayhanai.com";

/**
 * Support line — **WhatsApp only**, it is not a callable phone number. Every
 * surface that renders it must carry the «واتساب فقط» qualifier below so nobody
 * tries to dial it, and must wrap the digits in `dir="ltr"` so the leading «+»
 * stays on the left inside our RTL shell. Latin digits only (app-wide policy).
 */
export const SUPPORT_WHATSAPP = "+966552517086";

/** Qualifier rendered next to the number — never show the number without it. */
export const SUPPORT_WHATSAPP_NOTE = "واتساب فقط";

/** wa.me wants the number bare: no «+», no spaces, no dashes. */
export const SUPPORT_WHATSAPP_HREF = "https://wa.me/966552517086";

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

export const HERO = {
  badge: "مساعد المحامي السعودي",
  // Split so the differentiator clause renders in the brand color.
  titleLead: "من البحث القانوني إلى المذكرة الجاهزة،",
  titleEmphasis: "موثّقة بالأنظمة والأحكام",
  subtitle:
    "ريحان يبحث لك في الأنظمة واللوائح وأكثر من 30,000 حكم قضائي، ثم يصوغ مذكرتك أو لائحتك بلغة قانونية دقيقة — وكل استشهاد فيها مربوط بمصدره الرسمي ورابطه المباشر.",
  primaryCta: "جرّب ريحان مجاناً",
  secondaryCta: "شاهد مثالاً حقيقياً",
} as const;

/** Compact data-moat strip shown under the hero CTAs — front-loads credibility
 *  so the corpus scale is visible above the fold. */
export const HERO_TRUST: { value: string; label: string }[] = [
  { value: "+3,500", label: "نظام ولائحة ودليل" },
  { value: "+30,000", label: "حكم قضائي" },
  { value: "+1,800", label: "تعميم رسمي" },
];

// ---------------------------------------------------------------------------
// Comparison — Rayhan vs. general-purpose AI (ChatGPT & friends)
//
// Head-to-head table. Tone stays gain-framed: the "others" column is neutral,
// not fear-mongering — general tools are fine for general questions, they just
// weren't built for Saudi legal work. Each row is one dimension the lawyer feels.
// ---------------------------------------------------------------------------

export const COMPARISON_HEADER = {
  title: "ريحان مقابل الأدوات العامة",
  subtitle:
    "أدوات الذكاء الاصطناعي العامة مفيدة للأسئلة اليومية، لكنها لم تُصمَّم للعمل القانوني السعودي. هذا هو الفرق حين تكون الدقة والمصدر أساس عملك.",
  rayhanLabel: "ريحان",
  rayhanHint: "مساعد قانوني سعودي متخصص",
  othersLabel: "الأدوات العامة",
  othersHint: "ChatGPT وأمثالها",
} as const;

export interface ComparisonRow {
  icon: LucideIcon;
  dimension: string;
  /** Rayhan's answer — the ✓ column. */
  rayhan: string;
  /** General-purpose tools — the ✗ column. */
  others: string;
}

export const COMPARISON: ComparisonRow[] = [
  {
    icon: ShieldCheck,
    dimension: "دقّة المصادر",
    rayhan:
      "كل معلومة ورقم مربوطان بمصدرهما الرسمي ورابطه المباشر — بلا هلوسة.",
    others: "قد يستشهد بأنظمة أو أرقام غير حقيقية يصعب التحقّق منها.",
  },
  {
    icon: Library,
    dimension: "تغطية الأنظمة السعودية",
    rayhan:
      "أكثر من 3,500 نظام ولائحة، و30,000 حكم قضائي، و1,800 تعميم رسمي.",
    others: "يُلمّ بالأنظمة الشهيرة فقط كنظام العمل، وتغيب عنه بقية المصادر.",
  },
  {
    icon: PenLine,
    dimension: "التخصّص لعمل المحامي",
    rayhan:
      "وكيل بحث ووكيل صياغة متخصّصان، مع إمكانية إضافة قوالبك الخاصة.",
    others: "أداة عامة لا تستهدف احتياجات المحامي ولا سير عمله.",
  },
  {
    icon: ScanText,
    dimension: "استخراج بيانات المستندات",
    rayhan:
      "يستخرج الأسماء والأرقام من مستنداتك بدقة تصل إلى 99٪ للملفات الواضحة (OCR).",
    others: "لا يستخرج الأسماء والأرقام من المستندات بدقة.",
  },
];

// ---------------------------------------------------------------------------
// Data-moat stats band
// ---------------------------------------------------------------------------

export interface Stat {
  value: string;
  label: string;
  /** Optional secondary line — e.g. the entities a source class comes from. */
  hint?: string;
}

export const STATS: Stat[] = [
  { value: "+3,500", label: "نظام ولائحة ودليل" },
  {
    value: "+1,800",
    label: "تعميم رسمي",
    hint: "وزارة العدل · هيئة الغذاء والدواء · البنك المركزي",
  },
  { value: "+30,000", label: "قضية وحكم قضائي" },
  { value: "+200", label: "كيان حكومي", hint: "مصادر مجمّعة" },
];

// ---------------------------------------------------------------------------
// Search-WI showcase — a REAL Rayhan output (blog share c6f6b05f…).
// The conclusion excerpt + citations are taken verbatim from a real answer so
// the showcase reflects the actual product, not a mock. This example cites 16
// sources across regulations AND government services.
//
// ⚠ THE خدمة حكومية CARDS BELONG HERE, AND THEY SURVIVED THE 2026-08-03 RETIREMENT
// OF THE COMPLIANCE WING. They were briefly removed with it and put back the same
// day, on purpose: a citation card is a NAME, A PROVIDER, ONE LINE AND THE
// ENTITY'S OWN LINK — it never restates الشروط / المستندات / الخطوات, which is the
// only thing the retirement was about. That makes this block the honest picture of
// what ريحان still does with a service: cite it and hand you its official page.
// Do not "tidy" them away again, and do not let a card here grow a body.
// ---------------------------------------------------------------------------

export const SHOWCASE = {
  eyebrow: "البحث القانوني",
  title: "بحثٌ يُظهر مصادره",
  subtitle:
    "كل تقرير يعطيك إجابة مكتملة، وكل استشهاد فيها مربوط بمصدره الرسمي ورابطه المباشر — من الأنظمة، والأحكام القضائية، والخدمات الحكومية.",
  exampleTag: "مثال حقيقي من ريحان",
  question:
    "كيف أقدر آخذ حقوقي من الشركة بعد فسخ العقد، وقد مضى على الفسخ أكثر من شهر؟",
  answerLead:
    "بعد فسخ العقد — ولا سيّما عقد العمل — يستحق الطرف المتضرر مجموعة من الحقوق المالية والإجرائية التي حدّدها النظام، ولا يُسقِط مرور أكثر من شهر على الفسخ هذه الحقوق؛ بل يصبح الطرف المخلّ ملزماً بتصفيتها والتعويض عن التأخير.",
  answerBody:
    "ومن أبرز هذه الحقوق مكافأة نهاية الخدمة: تُحسب على أساس أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة من السنوات التالية، ويُتّخذ الأجر الأخير أساساً لحسابها.",
  answerLeadCites: [] as number[],
  answerBodyCites: [1],
} as const;

/** The source types every search report can cite, each with the kind of official
 *  link its card carries. Mirrors ReferencePanel's DOMAIN_META. */
export interface SourceType {
  icon: LucideIcon;
  label: string;
  linkLabel: string;
  tint: string;
}

export const SOURCE_TYPES: SourceType[] = [
  {
    icon: Scale,
    label: "نظام",
    linkLabel: "رابط النظام الرسمي",
    tint: "text-sky-600 dark:text-sky-400",
  },
  {
    icon: Gavel,
    label: "قضية",
    linkLabel: "تفاصيل الحكم القضائي",
    tint: "text-amber-600 dark:text-amber-400",
  },
  {
    icon: Building2,
    label: "خدمة حكومية",
    // ⚠ NOT «رابط المنصة الوطنية». The portal link was removed from the product
    // on 2026-08-03 — `service_url`, the entity's own page, is the only exit a
    // service citation offers now, so this label names that and nothing else.
    linkLabel: "رابط الخدمة الرسمي",
    tint: "text-emerald-600 dark:text-emerald-400",
  },
];

/** Total sources the real answer cited — drives the "المراجع (16)" count. */
export const SHOWCASE_TOTAL_REFS = 16;

export interface ShowcaseCitation {
  n: number;
  label: string;
  // A serializable domain key (NOT an icon component) — this object crosses the
  // server→client boundary, and RSC can't serialize a function/component.
  domain: "regulations" | "cases" | "compliance";
  tint: string;
  title: string;
  /** Owning gov entity — shown for خدمة حكومية citations. */
  provider?: string;
  snippet: string;
  url: string;
  /** Full verbatim source text — when present, «عرض المصدر» opens it in a
   *  dialog (a live demo of the in-app source viewer). */
  sourceMd?: string;
}

/** Verbatim source text behind citation [1] — نظام العمل, مكافأة نهاية الخدمة
 *  (المواد 84–88). Exactly what the in-app «عرض المصدر» shows. */
const SOURCE_LABOR_LAW_EOS = `# الفصل الرابع

## مكافأة نهاية الخدمة

### المادة الرابعة والثمانون:

إذا انتهت علاقة العمل وجب على صاحب العمل أن يدفع إلى العامل مكافأة عن مدة خدمته تحسب على أساس أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة من السنوات التالية، ويتخذ الأجر الأخير أساساً لحساب المكافأة، ويستحق العامل مكافأة عن أجزاء السنة بنسبة ما قضاه منها في العمل.

### المادة الخامسة والثمانون:

إذا كان انتهاء علاقة العمل بسبب استقالة العامل يستحق في هذه الحالة ثلث المكافأة بعد خدمة لا تقل مدتها عن سنتين متتاليتين، ولا تزيد على خمس سنوات، ويستحق ثلثيها إذا زادت مدة خدمته على خمس سنوات متتالية ولم تبلغ عشر سنوات ويستحق المكافأة كاملة إذا بلغت مدة خدمته عشر سنوات فأكثر.

### المادة السادسة والثمانون:

استثناء من حكم المادة (الثامنة) من هذا النظام، يجوز الاتفاق على ألا تحسب في الأجر الذي تُسوى على أساسه مكافأة نهاية الخدمة جميع مبالغ العمولات أو بعضها والنسب المئوية عن ثمن المبيعات وما أشبه ذلك من عناصر الأجر الذي يدفع إلى العامل وتكون قابلة بطبيعتها للزيادة والنقص.

### المادة السابعة والثمانون:

استثناء مما ورد في المادة (الخامسة والثمانين) من هذا النظام تستحق المكافأة كاملة في حالة ترك العامل العمل نتيجة لقوة قاهرة خارجة عن إرادته، كما تستحقها العاملة إذا أنهت العقد خلال ستة أشهر من تاريخ عقد زواجها أو ثلاثة أشهر من تاريخ وضعها.

### المادة الثامنة والثمانون:

إذا انتهت خدمة العامل وجب على صاحب العمل دفع أجره وتصفية حقوقه خلال أسبوع - على الأكثر - من تاريخ انتهاء العلاقة العقدية. أما إذا كان العامل هو الذي أنهى العقد، وجب على صاحب العمل تصفية حقوقه كاملة خلال مدة لا تزيد على أسبوعين. ولصاحب العمل أن يحسم أي دين مستحق له بسبب العمل من المبالغ المستحقة للعامل.`;

/** A representative slice of the real example's 16 citations — one نظام + two
 *  خدمة حكومية, each with its verbatim official link. */
export const SHOWCASE_CITATIONS: ShowcaseCitation[] = [
  {
    n: 1,
    label: "نظام",
    domain: "regulations",
    tint: "text-sky-600 dark:text-sky-400",
    title: "نظام العمل",
    snippet:
      "مكافأة نهاية الخدمة: أجر نصف شهر عن كل سنة من السنوات الخمس الأولى، وأجر شهر عن كل سنة تالية، على أساس الأجر الأخير.",
    url: "https://laws.boe.gov.sa/boelaws/laws/lawdetails/08381293-6388-48e2-8ad2-a9a700f2aa94/1",
    sourceMd: SOURCE_LABOR_LAW_EOS,
  },
  // No `sourceMd` on either service card, and that is the point: «عرض المصدر»
  // renders only when one is present, so a service card offers exactly one
  // action — «فتح المصدر الرسمي», out to the issuing entity.
  {
    n: 16,
    label: "خدمة حكومية",
    domain: "compliance",
    tint: "text-emerald-600 dark:text-emerald-400",
    title: "إنهاء العلاقة التعاقدية",
    provider: "وزارة الموارد البشرية والتنمية الاجتماعية",
    snippet:
      "الخدمة الرسمية لإنهاء العلاقة التعاقدية بين صاحب العمل والعامل وإجراءاتها.",
    url: "https://hrsd.gov.sa/node/5573760",
  },
  {
    n: 19,
    label: "خدمة حكومية",
    domain: "compliance",
    tint: "text-emerald-600 dark:text-emerald-400",
    title: "الحاسبة العمالية",
    provider: "وزارة العدل",
    snippet:
      "حاسبة رسمية لاحتساب مستحقات العامل ومكافأة نهاية الخدمة بدقة.",
    url: "https://www.moj.gov.sa/ar/eServices/Pages/ServiceDetailsNew.aspx?itemId=299",
  },
];

// ---------------------------------------------------------------------------
// Shared shape for any report showcase. `ShowcaseReportCard` renders one of
// these; the /learn lessons keep the default (the labour-rights example above),
// the lawyer landing passes `LAWYER_SHOWCASE`.
// ---------------------------------------------------------------------------

export interface ShowcaseData {
  exampleTag: string;
  question: string;
  answerLead: string;
  answerLeadCites: readonly number[];
  answerBody: string;
  answerBodyCites: readonly number[];
  citations: ShowcaseCitation[];
  totalRefs: number;
}

export const DEFAULT_SHOWCASE: ShowcaseData = {
  exampleTag: SHOWCASE.exampleTag,
  question: SHOWCASE.question,
  answerLead: SHOWCASE.answerLead,
  answerLeadCites: SHOWCASE.answerLeadCites,
  answerBody: SHOWCASE.answerBody,
  answerBodyCites: SHOWCASE.answerBodyCites,
  citations: SHOWCASE_CITATIONS,
  totalRefs: SHOWCASE_TOTAL_REFS,
};

// ---------------------------------------------------------------------------
// Lawyer showcase — a REAL research report from a practising lawyer's account
// (workspace_items c7a487f9…, 2026-09-22, 10 references). The question is the
// lawyer's own with typing slips fixed; the answer and every citation are
// verbatim from the report, resolved from chunks_v2 / cases. No client data
// appears in this exchange — it is a pure point-of-law question.
// ---------------------------------------------------------------------------

/** Verbatim نظام المرافعات الشرعية, المادة 34 — citation [4] of the report. */
const SOURCE_MURAFAAT_34 = `##### المادة الرابعة والثلاثون

تختص المحاكم العمالية بالنظر في الآتي:

أ- المنازعات المتعلقة بعقود العمل والأجور والحقوق وإصابات العمل والتعويض عنها.

ب- المنازعات المتعلقة بإيقاع صاحب العمل الجزاءات التأديبية على العامل، أو المتعلقة بطلب الإعفاء منها.

ج- الدعاوى المرفوعة لإيقاع العقوبات المنصوص عليها في نظام العمل.

د- المنازعات المترتبة على الفصل من العمل.

هـ - شكاوى أصحاب العمل والعمال الذين لم تقبل اعتراضاتهم ضد أي قرار صادر من أي جهاز مختص في المؤسسة العامة للتأمينات الاجتماعية، يتعلق بوجوب التسجيل والاشتراكات أو التعويضات.

و- المنازعات المتعلقة بالعمال الخاضعين لأحكام نظام العمل، بمن في ذلك عمال الحكومة.

ز- المنازعات الناشئة عن تطبيق نظام العمل ونظام التأمينات الإجتماعية، دون إخلال باختصاصات المحاكم الأخرى وديوان المظالم.`;

export const LAWYER_SHOWCASE: ShowcaseData = {
  exampleTag: "مثال حقيقي من محادثة",
  question:
    "ابحث لي: مدير شركة ليس شريكاً لكنه معيّن بعقد التأسيس، ويطالب بحقوقه — هل يطالب بها وفق نظام الشركات أمام المحكمة التجارية، أم وفق نظام العمل أمام المحكمة العمالية؟ وابحث عن سوابق.",
  answerLead:
    "يختص القضاء العمالي – لا التجاري – بنظر دعوى مدير الشركة غير الشريك المُعيّن بعقد التأسيس عندما يطالب بحقوقه المالية (كالأجر والمكافأة والتعويض عن إنهاء العلاقة).",
  answerLeadCites: [7, 9],
  answerBody:
    "تختص المحاكم العمالية – وفق المادة 34 من نظام المرافعات الشرعية – بالنظر في المنازعات المتعلقة بعقود العمل والأجور والحقوق وإصابات العمل والتعويض عنها. ومجرد ورود اسم المدير في عقد التأسيس لا يجعله تاجراً أو شريكاً، وقد قضت المحاكم التجارية في أكثر من حكم بعدم اختصاصها نوعياً وأحالت هذه الدعاوى إلى المحاكم العمالية.",
  answerBodyCites: [4],
  totalRefs: 10,
  citations: [
    {
      n: 4,
      label: "نظام",
      domain: "regulations",
      tint: "text-sky-600 dark:text-sky-400",
      title: "نظام المرافعات الشرعية — المادة الرابعة والثلاثون",
      snippet:
        "تختص المحاكم العمالية بالنظر في المنازعات المتعلقة بعقود العمل والأجور والحقوق وإصابات العمل والتعويض عنها.",
      url: "https://laws.moj.gov.sa/ar/legislation/sSe-gyvwrajdndY5P08WZg",
      sourceMd: SOURCE_MURAFAAT_34,
    },
    {
      n: 7,
      label: "قضية",
      domain: "cases",
      tint: "text-amber-600 dark:text-amber-400",
      title: "المحكمة التجارية بالرياض — 22 ذو القعدة 1444هـ",
      snippet:
        "مطالبة مدير شركة بإثبات استقالته وإبراء ذمته ومكافأة مالية — قضت المحكمة بعدم اختصاصها نوعياً وأحالت الدعوى إلى المحاكم العمالية.",
      url: "https://laws.moj.gov.sa/ar/JudicialDecisionsList/0/g59twljUUvW65xDXqp9_Dxw5F_yILFB2pe21SUPEGsBDEjX_EXFz1y35Oa4UWPIf",
    },
    {
      n: 9,
      label: "قضية",
      domain: "cases",
      tint: "text-amber-600 dark:text-amber-400",
      title: "المحكمة التجارية بجدة — 30 صفر 1444هـ",
      snippet:
        "مطالبة بأجر عن إدارة شركة — تبيّن للمحكمة أن العلاقة عمالية لا تجارية، فقضت بعدم الاختصاص وأحالته للمحاكم العمالية.",
      url: "https://laws.moj.gov.sa/ar/JudicialDecisionsList/1/UrSRDEwfIODhCbyr7FFUq-7ATEz6RBsx2C5ZW-fbQ8DpoBHzzrY247DIuldqCTTc",
    },
  ],
};

// ---------------------------------------------------------------------------
// Drafting showcase — a REAL memo the writer produced in a lawyer's account
// (workspace_items d2fa0c56…, 2026-09-16), built from two earlier research
// reports in the same conversation, 21 references. The excerpt is verbatim
// except that citation numbers follow the memo's own «المراجع» order. The
// paragraphs chosen name NO party — keep it that way (client confidentiality);
// never swap in a paragraph that names a party or a case number.
// ---------------------------------------------------------------------------

export interface DraftParagraph {
  /** Optional bold run-in label («أولاً: …»). */
  lead?: string;
  text: string;
  cites: number[];
}

export const DRAFT_SHOWCASE = {
  eyebrow: "الصياغة",
  title: "مذكرتك تُبنى على بحثك",
  subtitle:
    "وكيل الصياغة يكتب المذكرة أو اللائحة على نتائج البحث في القضية نفسها، بلغة قانونية رسمية ومراجع مرقّمة — ثم تعدّلها بالمحادثة أو يدوياً.",
  exampleTag: "مثال حقيقي من محادثة",
  docType: "مذكرة قانونية",
  docTitle:
    "مذكرة قانونية في أثر مبدأ وحدة الحق بين الورثة على قطعية الحكم الابتدائي وجواز التماس إعادة النظر",
  builtFrom: "بُنيت على تقريرَي بحث سابقين في المحادثة نفسها",
  totalRefs: 21,
  wordCount: "1,174",
  sectionHeading: "الأصل الإجرائي — شخصية الاستئناف وقطعية الحكم",
  paragraphs: [
    {
      lead: "أولاً: مدة الاعتراض واكتساب القطعية.",
      text: "تنص المادة (94 بعد المائة) من نظام المرافعات الشرعية على أن مدة الاعتراض ثلاثون يوماً، فإذا لم يودع المعترض اعتراضه خلالها سقط حقّه. وتؤكد المادة (36) من اللائحة التنفيذية لطرق الاعتراض على الأحكام أنه إذا حكمت المحكمة بسقوط الحق في الاستئناف أو بعدم قبوله اكتسب الحكم المستأنف الصفة النهائية.",
      cites: [1, 2],
    },
    {
      lead: "ثانياً: نطاق الاستئناف الشخصي.",
      text: "تنص المادة (38) من اللائحة التنفيذية على أن «يكون تأييد حكم محكمة الدرجة الأولى—أو إلغاءه—حكماً صادراً من محكمة الاستئناف، وذلك فيما اعترض عليه» فقط. والمادة (82) من نظام المحاكم التجارية تقرر أن الاستئناف ينقل الدعوى بحالتها التي كانت عليها قبل صدور الحكم المستأنف «بالنسبة إلى ما رُفع عنه الاستئناف فقط».",
      cites: [3, 4],
    },
  ] as DraftParagraph[],
  /** What the lawyer can do with the finished document — shipped features only. */
  actions: ["النسخ لناجز", "تعديل بالمحادثة", "تحرير مباشر"],
} as const;

// ---------------------------------------------------------------------------
// Workflow strip — the lawyer's actual day, in four steps.
// ---------------------------------------------------------------------------

export interface WorkflowStep {
  icon: LucideIcon;
  title: string;
  body: string;
}

export const WORKFLOW_HEADER = {
  eyebrow: "سير العمل",
  title: "من ملف القضية إلى ناجز",
  subtitle:
    "كل خطوة في محادثة واحدة، وكل مخرَج يبقى في مساحة عمل القضية ليبني عليه ما بعده.",
} as const;

export const WORKFLOW: WorkflowStep[] = [
  {
    icon: FileUp,
    title: "ارفع ملف القضية",
    body: "صحيفة الدعوى والصكوك والعقود — يستخرج ريحان نصّها حتى من الملفات الممسوحة ضوئياً.",
  },
  {
    icon: Search,
    title: "ابحث في الأنظمة والأحكام",
    body: "وكيل البحث يوسّع سؤالك إلى عدة زوايا، ويعيد تقريراً مرقّم المراجع من المصادر الرسمية.",
  },
  {
    icon: PenLine,
    title: "صُغ المذكرة أو اللائحة",
    body: "وكيل الصياغة يكتب على نتائج بحثك، ويشير إلى مستنداتك بأرقامها «مرفق رقم 1».",
  },
  {
    icon: ClipboardCopy,
    title: "انسخ لناجز",
    body: "«النسخ لناجز» يعطيك نصاً نظيفاً بلا تنسيق، جاهزاً للّصق في حقول ناجز — مع المراجع.",
  },
];

// ---------------------------------------------------------------------------
// Confidentiality — the lawyer's first objection. Every claim here RESTATES
// /privacy and /masking (same rule as ForLawyersView): change those first.
// ---------------------------------------------------------------------------

export const CONFIDENTIALITY = {
  eyebrow: "سرّية الموكّل",
  title: "بيانات موكّليك تبقى سرّية",
  subtitle:
    "«وضع السرية» مفعّل افتراضياً: يستبدل أرقام الهوية والجوال والآيبان والبريد بأرقام بديلة قبل أي معالجة خارجية، ثم يعيد الأصل في إجابتك.",
  maskedFields: ["رقم الهوية", "الجوال", "الآيبان", "البريد الإلكتروني"],
} as const;

// ---------------------------------------------------------------------------
// Lawyer FAQ — the objections, answered short. Answers 2–3 are verbatim from
// ForLawyersView (which restates /privacy and /masking).
// ---------------------------------------------------------------------------

export interface FaqItem {
  q: string;
  a: string;
  link?: { label: string; href: string };
}

export const LAWYER_FAQ: FaqItem[] = [
  {
    q: "هل يأخذ ريحان مكان المحامي؟",
    a: "لا. ريحان يختصر ساعات البحث والمسودة الأولى، ويبقى التكييف والقرار والتوقيع لك. والوقت الذي توفّره يعود إليك — لموكّل جديد، أو لقضية خارج تخصّصك كنت ستعتذر عنها.",
    link: { label: "ريحان للقانونيين", href: "/for-lawyers" },
  },
  {
    q: "هل يأخذ ريحان معرفتي ويستخدمها؟",
    a: "لا نستخدم محتواك المُدخَل لتدريب نماذج ذكاء اصطناعي عامة أو لمصلحة الغير، ولا نُتيحه لهذا الغرض دون موافقتك الصريحة. قوالبك ومذكراتك وأسلوبك في الصياغة تبقى في حسابك وحده، ولا يُرسَل إلى مزوّدي النماذج إلا ما يلزم لإنتاج ما طلبته أنت.",
    link: { label: "كيف نحمي بياناتك", href: "/learn/data-protection" },
  },
  {
    q: "هل يستخدم بيانات عملائي؟",
    a: "بيانات عملائك لا تغادر خوادمنا إلا للمعالجة اللازمة. و«وضع السرية» مفعّل افتراضياً: يستبدل أرقام الهوية والجوال والآيبان والبريد بأرقام بديلة قبل أي معالجة خارجية، ثم يعيد الأصل في إجابتك.",
    link: { label: "تقنيع المعرّفات", href: "/masking" },
  },
  {
    q: "كيف أتحقّق مما يكتبه؟",
    a: "كل استشهاد في التقرير أو المذكرة مرقّم ومربوط بمصدره: «عرض المصدر» يفتح النص الحرفي للمادة أو الحكم، و«فتح المصدر الرسمي» ينقلك إلى صفحته لدى الجهة المختصة.",
  },
];
