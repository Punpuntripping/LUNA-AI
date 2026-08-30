// Single source of truth for the global public-site header (and the footer nav
// columns, which mirror it). Pure data — server-safe, no "use client", no imports
// beyond types and sibling route constants — so both the server `SiteFooter` and
// the client `SiteNav` / `SiteMobileNav` read the same list.
//
// The `enabled` flag on each child is the feature gate: a section renders in the
// header ONLY when its flag is true. Each later phase flips exactly one or two
// flags here and the header rearranges itself with no component change — see
// `resolve-nav.ts` for the auto-promote rule (0 enabled → hidden / 1 → flat link
// / 2+ → dropdown).
//
// ORDER HERE IS LOGICAL, NOT VISUAL. The desktop bar renders this list REVERSED
// so it reads left-to-right (عن ريحان nearest the auth buttons, الباقات والأسعار
// nearest the brand) — see `SiteNav`. The mobile drawer and the footer use this
// order as written, top-to-bottom.
//
//   • عن ريحان            — hub at /about_us AND a dropdown over the pitch pages,
//                            with الوثائق النظامية (الخصوصية · الشروط) folded in
//                            as a second section. Kept for signed-in visitors too:
//                            the pitch pages stay useful after signup, and dropping
//                            the slot mid-session made it visibly vanish once the
//                            auth probe resolved.
//   • اكتشف ريحان         — how-to / guides. Hub at /learn. Children Phase C, so
//                            it resolves to a flat link until two of them land.
//   • المكتبة القانونية   — the SEO corpus dropdown + المدونة, headed by the
//                            /library hub row (restored — library_sectors D12).
//                            Corpus children ship disabled; seo_public_library
//                            flips them phase by phase.
//   • الباقات والأسعار    — flat link, always visible (upgrade path when authed).
//
// The standalone «السياسات» slot was REMOVED 2026-08-02. Two legal rows did not
// earn a top-level slot next to four content slots, and the settings popover had
// already folded them under عن ريحان in the 5→3 pass — the header was the only
// surface still splitting them out. `/terms` and `/privacy` are unchanged; only
// the nav path to them moved.

import { LEGAL_ROUTES } from "@/lib/legal";

/** Group heading a child sits under inside a dropdown. Consecutive children that
 *  share a `section` render under one heading; a change of section draws a divider.
 *
 *  EVERY dropdown's children carry one, so all panels share the same anatomy:
 *  heading → rows of (label + description). A panel with no heading read as a
 *  different component sitting in the same menu bar. */
export type NavSection =
  | "المصادر الرسمية"
  | "مقالات وتحليلات"
  | "تعرّف على ريحان"
  | "الوثائق النظامية";

export interface NavChild {
  label: string;
  href: string;
  /** One-line description shown under the label in the desktop dropdown. */
  description?: string;
  /** Optional heading grouping within the dropdown. */
  section?: NavSection;
  /** Rendered only when true. Each content/SEO phase flips its own flag. */
  enabled: boolean;
}

export interface NavGroup {
  /** Top-level slot label. */
  label: string;
  /**
   * Optional hub page the slot points at. When present the slot is ALWAYS shown:
   * a flat link to the hub while it has < 2 enabled children, or a dropdown whose
   * header links to the hub once it has 2+. Without an `href`, a slot with < 2
   * enabled children collapses to its single child or disappears entirely.
   */
  href?: string;
  /**
   * Label for the hub link that heads the dropdown panel. Defaults to
   * «كل {label}», which only reads well when the label is a noun phrase — a slot
   * like «عن ريحان» needs its own wording.
   */
  hubLabel?: string;
  /**
   * Description under the hub row. Without it that row is the only one in any
   * panel with a bare label, which is exactly the kind of odd-one-out the
   * shared anatomy is meant to prevent.
   */
  hubDescription?: string;
  /** Drop this slot for signed-in visitors (it only sells the pre-signup pitch). */
  hideWhenAuthed?: boolean;
  children?: NavChild[];
}

export const SITE_NAV: NavGroup[] = [
  {
    label: "عن ريحان",
    href: "/about_us",
    hubLabel: "عن ريحان",
    hubDescription: "قصة ريحان ومهمّته",
    hideWhenAuthed: false,
    children: [
      {
        label: "لمن ريحان؟",
        href: "/audiences",
        description: "القطاعات والفئات التي بُني ريحان لخدمتها",
        section: "تعرّف على ريحان",
        enabled: true,
      },
      // Sits between the two on purpose: «لمن ريحان؟» answers *who*, this one
      // goes deep on the primary audience, and «ريحان مقابل ChatGPT» closes with
      // *why not the alternative*. Anywhere before the legal rows is safe —
      // `groupChildrenBySection` buckets by CONTIGUOUS runs, so only those two
      // must stay last and adjacent.
      {
        label: "ريحان للقانونيين",
        href: "/for-lawyers",
        description: "هل يأخذ الذكاء الاصطناعي مكان المحامي؟",
        section: "تعرّف على ريحان",
        enabled: true,
      },
      {
        label: "ريحان مقابل ChatGPT",
        href: "/vs-chatgpt",
        description: "لماذا لا تكفي الأدوات العامة للعمل القانوني السعودي",
        section: "تعرّف على ريحان",
        enabled: true,
      },
      // Folded in from the deleted «السياسات» slot. `groupChildrenBySection`
      // buckets by CONTIGUOUS runs, so these two must stay last and adjacent or
      // the الوثائق النظامية heading splits into two headings.
      {
        label: "الخصوصية والسياسة العامة",
        href: LEGAL_ROUTES.privacy,
        description: "كيف نجمع بياناتك ونحميها",
        section: "الوثائق النظامية",
        enabled: true,
      },
      {
        label: "الشروط والأحكام",
        href: LEGAL_ROUTES.terms,
        description: "شروط استخدام ريحان",
        section: "الوثائق النظامية",
        enabled: true,
      },
    ],
  },
  {
    label: "اكتشف ريحان",
    href: "/learn",
    hubLabel: "اكتشف ريحان",
    hubDescription: "كل الأدلة في مكان واحد",
    children: [
      {
        label: "كيف يعمل ريحان",
        href: "/learn/how-it-works",
        description: "من السؤال إلى التقرير الموثّق",
        enabled: true,
      },
      {
        label: "مساحة العمل",
        href: "/learn/workspace",
        description: "ذاكرة موثّقة لمخرجات محادثتك",
        enabled: true,
      },
      {
        label: "حماية البيانات",
        href: "/learn/data-protection",
        description: "خوادمنا، شركاؤنا، وتقنيع المعرّفات",
        enabled: true,
      },
      // A lesson, not a legal document — it explains what a نقطة buys and why the
      // caps sit where they do, so it belongs beside «كيف يعمل ريحان», not under
      // الوثائق النظامية. /pricing states the allowances; this page explains them.
      {
        label: "سياسة حد الاستخدام",
        href: "/learn/usage-limits",
        description: "ما الذي تستهلكه كل عملية، ولماذا الحدود عادلة",
        enabled: true,
      },
      {
        label: "دليل الاستخدام",
        href: "/learn/guide",
        description: "خطوة بخطوة لأفضل النتائج",
        enabled: false,
      },
      {
        label: "أفضل الممارسات",
        href: "/learn/best-practices",
        description: "كيف تصيغ سؤالك القانوني بدقة",
        enabled: false,
      },
      {
        label: "أمثلة أسئلة",
        href: "/learn/examples",
        description: "نماذج أسئلة حقيقية وإجاباتها",
        enabled: false,
      },
    ],
  },
  {
    // The hub row is BACK (library_sectors.md D12, reversing the 2026-07-23
    // removal). It was dropped when /library was a `ComingSoonHub` placeholder
    // — a menu row that went somewhere less specific than every row under it.
    // /library now carries what no single wing can: the 38-sector browse grid,
    // which is the only cross-corpus axis and the only crawlable path into
    // `/library/{sector}`. That is more specific than the wings, not less.
    label: "المكتبة القانونية",
    href: "/library",
    hubLabel: "كل المكتبة القانونية",
    hubDescription: "تصفّح المكتبة كاملة، أو حسب القطاع",
    children: [
      {
        label: "المدونة",
        href: "/blog",
        description: "مقالات وتحليلات قانونية",
        section: "مقالات وتحليلات",
        enabled: true,
      },
      {
        label: "الأنظمة واللوائح",
        href: "/regulations",
        description: "أكثر من 3,000 نظام ولائحة ودليل",
        section: "المصادر الرسمية",
        enabled: true,
      },
      {
        label: "الأحكام القضائية",
        href: "/judgments",
        description: "أكثر من 20,000 حكم قضائي",
        section: "المصادر الرسمية",
        enabled: true,
      },
      {
        label: "التعاميم",
        href: "/circulars",
        description: "التعاميم الرسمية من الجهات الحكومية",
        section: "المصادر الرسمية",
        enabled: true,
      },
      // UNPARKED 2026-08-20: the pilot is over. All 169 guides are published and
      // indexable (`seo_item_meta`, content_type='compliance' — 169 slugs, none
      // blank, none falling back to `service-{ref}`), so the header slot now
      // promises what the wing actually holds.
      {
        label: "دليل الخدمات الحكومية",
        href: "/compliance",
        description: "أدلة مصوّرة خطوة بخطوة لأكثر الخدمات استخداماً",
        section: "المصادر الرسمية",
        enabled: true,
      },
      // Still parked — /forms awaits the drafts review, /calculators its phase.
      // Flip `enabled` and they slot into المصادر الرسمية with no other change.
      {
        label: "النماذج والصيغ",
        href: "/forms",
        description: "صيغ ومذكرات ونماذج جاهزة",
        section: "المصادر الرسمية",
        enabled: false,
      },
      {
        label: "الحاسبات القانونية",
        href: "/calculators",
        description: "حاسبات مكافأة نهاية الخدمة والمواعيد وغيرها",
        section: "المصادر الرسمية",
        enabled: false,
      },
    ],
  },
  {
    label: "الباقات والأسعار",
    href: "/pricing",
  },
];
