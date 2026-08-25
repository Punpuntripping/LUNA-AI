// Shared prop + data types for the SEO Public Library block component library
// (`components/library/blocks/`). Every block is PRESENTATIONAL — typed props
// in, JSX out, no data fetching. Page routes (Phase 2+) map backend payloads
// onto these shapes. Keep everything Arabic-first / RTL where user-visible.
//
// Spec: .claude/plans/seo_public_library.md — "PAGE TEMPLATES & BLOCK SYSTEM".

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

// Type-only, and it must stay that way: `lib/library/api.ts` imports the
// judgment shapes FROM this file, so a value import in either direction would
// be a runtime cycle. `import type` is erased at compile time, so the two
// modules only ever meet in the type checker. The three hub-item shapes live
// there because they are wire types owned by the fetchers; the doc payloads
// below reference them because a related-items strip is literally a list of hub
// cards (read_next_related_items §5.1).
import type { RegulationHubItem } from "@/lib/library/api";
// Same rule, same reason: type-only. `lib/library/legal-text.tsx` is a value
// module (it renders JSX), so importing it for real would drag a component
// module into a pure type file.
import type { LegalTableMap } from "@/lib/library/legal-text";

// ------------------------------------------------------------------
// Shared data primitives
// ------------------------------------------------------------------

/** One row in a metadata / «المعلومات الأساسية» grid. Optional internal link. */
export interface MetadataItem {
  label: string;
  value: string;
  href?: string;
}

/**
 * Lifecycle status of a regulation/document.
 *   active   → ساري (green)
 *   amended  → معدَّل (amber)
 *   repealed → ملغي (red, must be unmissable — never render a repealed law as
 *              current; hard rule from the plan).
 */
export type DocStatus = "active" | "amended" | "repealed";

/** One entry in a table-of-contents / «محتويات النظام». */
export interface TocEntry {
  id: string;
  label: string;
  /**
   * Anchor/link target. OPTIONAL (added Phase 2, backward-compatible): a locked
   * TOC row omits it and renders as non-interactive text instead of a `<Link>`.
   * Existing callers that always pass `href` are unaffected.
   */
  href?: string;
  /** Nesting depth (1 = top). Drives indentation. Default 1. */
  level?: number;
  /**
   * When true, the entry renders as muted, non-interactive text with a lock
   * indicator (a gated regulation section a signed-out reader can't open yet).
   * Added Phase 2 — backward-compatible (undefined = today's linked behavior).
   */
  locked?: boolean;
}

/** The content type a mesh/reference link points at — drives the kind icon. */
export type ReferenceKind =
  | "regulation"
  | "article"
  | "judgment"
  | "circular"
  | "service"
  | "blog"
  | "form"
  | "external";

/** One cited-source / cross-reference mesh link. */
export interface ReferenceItem {
  title: string;
  href: string;
  kind: ReferenceKind;
}

/** One outbound official source (BOE, ناجز, …). */
export interface OfficialSourceLink {
  label: string;
  href: string;
}

/** One question/answer pair for the FAQ accordion + FAQPage JSON-LD. */
export interface FaqItem {
  q: string;
  a: string;
}

/** One breadcrumb crumb. Current page usually omits `href`. */
export interface BreadcrumbItem {
  label: string;
  href?: string;
}

/** A topic chip (small pill link to a `/topics/{slug}` hub). */
export interface TopicChip {
  label: string;
  href: string;
}

/**
 * Gate decision surfaced to the client. The hidden text is NEVER shipped —
 * `hiddenPlaceholderLines` only tells the GateBanner how many DECORATIVE
 * skeleton bars to draw. Truncation happens server-side in `library_service`.
 */
export interface GateInfo {
  isTruncated: boolean;
  hiddenPlaceholderLines: number;
  /**
   * Where the «سجّل مجاناً» CTA links. OMIT for signup — `GateBanner` then
   * builds `/login?next=<page>&mode=register` from the pathname itself. Set it
   * only to aim the card somewhere that is not signup.
   */
  ctaHref?: string;
}

// ------------------------------------------------------------------
// Judgments (الأحكام القضائية) — wire payloads
// ------------------------------------------------------------------
//
// The other wings keep their wire types next to their fetchers in
// `lib/library/api.ts`. The judgments payloads live HERE instead (they are
// consumed by the hub view, the cards, the doc page and the cited-regulations
// block alike) — and they are declared SELF-CONTAINED on purpose: `lib/library/
// api.ts` already imports `DocStatus` from this file, so importing
// `DocMetadataRow` / `RegulationOfficialSource` back out of it would create a
// types↔lib import cycle. The two duplicated row shapes below are three lines
// each; the cycle would be forever.

/**
 * Canonical `cases.court_level` vocabulary — the TS mirror of
 * `agents/deep_search_v4/shared/court_levels.py`. THREE values, not two: every
 * ad-hoc two-branch copy of this in the Python side silently relabelled all 125
 * supreme-court rulings as ابتدائي. Wire fields stay `string` (a future DB value
 * must not break a page); this union types the FILTER vocabulary the UI offers.
 */
export type CourtLevel = "first_instance" | "appeal" | "supreme";

/** One label/value row in a judgment's «المعلومات الأساسية» card. */
export interface JudgmentMetadataRow {
  label: string;
  value: string;
}

/** One outbound official source on a judgment page (ناجز، وزارة العدل، …). */
export interface JudgmentOfficialSource {
  title: string;
  href: string;
}

/** One card in the /judgments hub grid. */
export interface JudgmentHubItem {
  slug: string;
  /** The composed listing title (subject + court + year). */
  title: string;
  court: string;
  /** Raw enum: `first_instance` | `appeal` | `supreme`. */
  court_level: string;
  /** Backend-rendered Arabic label for `court_level` — it owns the vocabulary. */
  court_level_label: string;
  city: string | null;
  date_hijri: string | null;
  date_gregorian: string | null;
  domains: string[];
  snippet: string;
}

/**
 * The /judgments hub envelope. NOTE the shape differs from the shared
 * `LibraryHubResponse` used by the other wings: the judgments contract specifies
 * only `{ items, page, total_pages }`. `cap_reached` / `max_anon_page` are typed
 * OPTIONAL so the anon depth-cap wall lights up automatically if/when the
 * backend starts emitting them (like every sibling hub does) — and, until then,
 * `?? false` simply means "no cap" and the hub paginates normally.
 */
export interface JudgmentHubResponse {
  items: JudgmentHubItem[];
  page: number;
  total_pages: number;
  cap_reached?: boolean;
  /** The CALLER's browse-depth cap (access tiers). See `LibraryHubResponse`. */
  max_page?: number;
  /** @deprecated same value as `max_page`; removed after one release. */
  max_anon_page?: number;
}

/**
 * One rendered section of a judgment. `text` is ALREADY gate-truncated
 * server-side — the hidden bytes never reach the client; `hidden_placeholder_
 * lines` only sizes the GateBanner skeleton. Section ids/labels, in order:
 *   facts الوقائع (free) · claims الطلبات · plaintiff_grounds أسانيد المدعي ·
 *   defendant_response رد المدعى عليه · defendant_grounds أسانيد المدعى عليه ·
 *   reasoning الأسباب والتسبيب · ruling المنطوق (free) ·
 *   objection_grounds أسباب الاعتراض · appellee_response رد المستأنف ضده ·
 *   appeal_reasoning تسبيب حكم الاستئناف · appeal_ruling منطوق حكم الاستئناف (free)
 * Empty sections are omitted by the backend, so never assume the full list.
 */
export interface JudgmentSection {
  id: string;
  title: string;
  text: string;
  is_truncated: boolean;
  hidden_placeholder_lines: number;
  /**
   * This section reached the reader WHOLE. One document-wide budget is spent
   * across sections in reading order, so this describes where the allowance ran
   * out — not a fixed free/gated layer. `is_truncated` drives the render.
   */
  is_free: boolean;
}

/**
 * Full /judgments/{slug} payload. `subject` is the H1; `title` is the longer
 * composed listing/meta title (subject + court + year). `summary_md` is ALWAYS
 * free (the ranking lead); `sections` carry the gated body.
 */
export interface JudgmentDoc {
  slug: string;
  title: string;
  subject: string;
  court: string;
  court_level: string;
  court_level_label: string;
  city: string | null;
  case_number: string | null;
  judgment_number: string | null;
  date_hijri: string | null;
  date_gregorian: string | null;
  hijri_year: number | null;
  appeal_result: string | null;
  domains: string[];
  metadata: JudgmentMetadataRow[];
  summary_md: string | null;
  /**
   * Does a «ملخص ريحان» (`cases.summary` — the structured AI summary of the
   * ruling) exist for this judgment. The summary TEXT is gated and never rides
   * this payload; it arrives only with the metered reveal, on the same unlock as
   * the body. This flag is what decides whether the reveal button renders at
   * all, so the ~18 summary-less rulings offer no action.
   */
  has_summary: boolean;
  sections: JudgmentSection[];
  /**
   * «اقرأ تاليًا» — up to 7 OTHER أحكام, same type only (D2), already publish-
   * filtered and ungated. Absent/empty on most rulings by design: 7,483 of the
   * 10,000 slugged judgments sit in المحكمة التجارية, where nothing clears the
   * relevance floor, and a missing strip beats six arbitrary neighbours.
   *
   * OPTIONAL on the wire — a page baked before the backend shipped simply has
   * no field, and that must cost the strip, never the render.
   */
  related_next?: JudgmentHubItem[];
  /**
   * «الأنظمة المذكورة» — one card per cited نظام, resolved and slug-filtered
   * server-side, capped at 7. One entry PER REGULATION, not per مادة (D8), and
   * unresolved citations are dropped (D9), so every entry has a page to point
   * at. Nothing here appears in `related_next` too (D13).
   *
   * OPTIONAL for a second reason as well: the payload SHAPE changed. It used to
   * be `{title, article_no, reg_slug, article_slug}[]`, and a page baked before
   * the backend shipped carries the OLD objects for a further 24h of ISR. The
   * judgment page filters on `slug` for exactly that reason — a stale entry has
   * none, so the strip degrades to absent instead of to broken hrefs.
   */
  cited_regulations?: RegulationHubItem[];
  /**
   * @deprecated Total citations found. Was the «+{n} … سجّل» tail on the old
   * `CitedRegulations` list; «الأنظمة المذكورة» has no gated tail, so nothing
   * reads it. Kept typed (optional) only so a payload that still sends it type-
   * checks.
   */
  cited_total?: number;
  official_sources: JudgmentOfficialSource[];
  /**
   * The gate AFTER the exposure budget decides: a ruling too short to gate
   * honestly reports "open" and ships whole, with no CTA.
   */
  gate_effective: "open" | "gated";
  /**
   * May a crawler have this ruling — `seo_item_meta.indexable` (migration 130).
   * 3,000 of the 10,000 published judgments carry it: the PDPL-cleared, diversity-
   * selected set that `/sitemaps/judgments` lists. The page renders its `robots`
   * meta from THIS field, which is what keeps the sitemap and the meta tag from
   * contradicting each other.
   *
   * NOT a gate. An indexable ruling is still gate-truncated — Googlebot sees the
   * same withheld body an anonymous human sees. `gate_effective` answers "how
   * much of it is free"; this answers "may it be crawled at all".
   */
  indexable: boolean;
  /** Sections actually truncated — sizes the placeholder bars and the CTA. */
  hidden_section_count: number;
  /** Bytes withheld server-side. The real exposure measure, unlike the count above. */
  withheld_chars: number;
  /** `withheld_chars` as a % of the ruling. */
  withheld_pct: number;
}

/** The page type a page belongs to — used by AskRayhanWidget context params. */
export type LibraryPageType =
  | "regulation"
  | "article"
  | "judgment"
  | "circular"
  | "compliance"
  | "form"
  | "blog"
  | "calculator"
  | "topic";

// ------------------------------------------------------------------
// Per-block prop interfaces
// ------------------------------------------------------------------

export interface TopicBreadcrumbsProps {
  /** Ordered crumbs from root → current page. */
  items: BreadcrumbItem[];
  /** Optional topic chips rendered under the trail. */
  chips?: TopicChip[];
  className?: string;
}

export interface TrustLineProps {
  /** ISO date string of the last content update. */
  updatedAt: string;
  /** Issuing/attribution entity, e.g. «هيئة الخبراء بمجلس الوزراء». */
  entity?: string;
  /** AI-disclaimer link target. Default = the shared legal disclaimer route. */
  disclaimerHref?: string;
  className?: string;
}

export interface MetadataCardProps {
  items: MetadataItem[];
  /** Optional status badge shown in the card header. */
  status?: DocStatus;
  /** Card heading. Default «المعلومات الأساسية». */
  title?: string;
  /**
   * An action rendered below the grid, behind a divider — the judgment page's
   * «ملخص ريحان» reveal. A client component may be passed here from a server
   * page; the card itself stays a server component.
   */
  footer?: ReactNode;
  className?: string;
}

export interface StatusBadgeProps {
  status: DocStatus;
  className?: string;
}

export interface CourtLevelBadgeProps {
  /** Raw `court_level` enum value. Unknown values render no badge. */
  level: string;
  /**
   * Backend-supplied Arabic label (`court_level_label`). Preferred over the
   * local map — the backend owns the display vocabulary.
   */
  label?: string | null;
  className?: string;
}

export interface LeadSummaryProps {
  /**
   * The lead summary. Blank-line-separated paragraphs are each rendered as a
   * `<p>`. Provide EITHER `text` or `children`.
   */
  text?: string;
  children?: ReactNode;
  /**
   * When the FIRST rendered block is a heading that duplicates this value
   * (colon/whitespace-insensitive), drop it — used where a styled section
   * `<h2>` already renders the same title the summary text repeats.
   */
  dedupeHeading?: string;
  className?: string;
}

export interface TocListProps {
  entries: TocEntry[];
  /** Heading. Default «محتويات النظام». */
  title?: string;
  /** Collapse behind a `<summary>` on mobile. Default true. */
  collapsible?: boolean;
  /**
   * Whether the `<details>` starts expanded. Default true (the historical
   * behaviour). Document pages pass `false`: their TOC runs to hundreds of
   * مواد, and expanded-by-default buries the article text below all of them.
   */
  defaultOpen?: boolean;
  /** Optional count pill in the header, e.g. «391 مادة». */
  badge?: string;
  className?: string;
}

export interface TocRailProps {
  entries: TocEntry[];
  /** Panel heading. Default «محتويات النظام». */
  title?: string;
  /** Count pill in the header, e.g. «391 مادة». */
  badge?: string;
  className?: string;
}

export interface TocFloatingProps {
  /** The SAME entries the page hands `TocList` — no extra server work. */
  entries: TocEntry[];
  /** Sheet heading + pill fallback label. Default «محتويات النظام». */
  title?: string;
  /** Count pill in the sheet header, e.g. «391 مادة». */
  badge?: string;
}

export interface ArticleBodyProps {
  /**
   * The VISIBLE text only (already server-truncated for gated items). Rendered
   * as markdown by default.
   */
  visibleText: string;
  /** When present + `isTruncated`, a GateBanner renders right after the body. */
  gate?: GateInfo;
  /** Render `visibleText` as plain blank-line paragraphs instead of markdown. */
  plain?: boolean;
  /**
   * `plain` only: sanitized table markup keyed by the `TBL_…` token that stands
   * in for it inside `visibleText`. Each resolved token renders as a real grid;
   * an unresolved one renders as NOTHING (never a raw token). Omit it — as
   * every non-regulation caller does, and as any payload baked before the
   * backend shipped this forces — and the body renders exactly as before.
   */
  tables?: LegalTableMap;
  /**
   * `plain` only: when the FIRST rendered block is a heading that duplicates
   * this value (colon/whitespace-insensitive), drop it — used where a styled
   * section `<h2>` already renders the same title the body text repeats.
   */
  dedupeHeading?: string;
  /**
   * Render the trailing GateBanner as decorative bars WITHOUT its CTA card, so
   * a single document-level GateBanner owns the one conversion card. Only has
   * an effect when the body is truncated.
   */
  gateBarsOnly?: boolean;
  /**
   * Markdown path only: emit deterministic `slugifyHeading` ids on `h1..h6` so a
   * table of contents can link INTO the body. Opt-in — default off keeps every
   * existing caller byte-identical. Only meaningful where the TOC's hrefs are
   * built from the SAME slugger, or the anchors dead-link.
   */
  headingAnchors?: boolean;
  className?: string;
}

export interface GateBannerProps {
  /** How many decorative skeleton bars to draw (purely cosmetic). */
  hiddenPlaceholderLines: number;
  /**
   * CTA link target. OMIT IT for the signup card — the banner then builds
   * `/login?next=<this page>&mode=register` from `usePathname()` itself, which
   * is the only form that opens the form on signup (so `signup_started` can
   * fire) and returns the new account to the page it was reading. Pass a value
   * only to point the card somewhere that is NOT signup, e.g. `/pricing`.
   */
  ctaHref?: string;
  /** CTA card headline. Default «سجّل مجاناً لعرض المحتوى كاملاً». */
  ctaLabel?: string;
  /**
   * Render ONLY the faded skeleton bars, with no CTA card — for per-section
   * gates when a single document-level GateBanner is the one conversion card.
   */
  barsOnly?: boolean;
  className?: string;
}

export interface FaqBlockProps {
  items: FaqItem[];
  /** Heading. Default «الأسئلة الشائعة». */
  title?: string;
  /** Emit FAQPage JSON-LD. Default true. */
  withJsonLd?: boolean;
  className?: string;
}

export interface ReferencesMeshProps {
  items: ReferenceItem[];
  /** Heading. Default «استند إلى». */
  title?: string;
  /** Count of additional gated references (renders a «+{n} … سجّل» tail). */
  gatedCount?: number;
  /** CTA target for the gated tail. Default "/login". */
  gateCtaHref?: string;
  className?: string;
}

/**
 * `RelatedStrip` — the shared frame behind «الأنظمة المذكورة» and «اقرأ تاليًا».
 *
 * It takes CARDS, not data: `children` is one existing hub card per related
 * item, so the strip never learns the four wire shapes and no second card
 * design exists. Replaced `CitedRegulationsProps` (the judgment-only citation
 * list) — see `RelatedStrip.tsx`.
 */
export interface RelatedStripProps {
  /** Heading text. Fixed per surface (D11) — never composed from the document. */
  title: string;
  /** One hub card per related item. An empty list renders NOTHING at all. */
  children: ReactNode;
  /** Heading icon. Default `BookMarked`. */
  icon?: LucideIcon;
  className?: string;
}

export interface OfficialSourcesProps {
  sources: OfficialSourceLink[];
  /** Heading. Default «المصادر الرسمية». */
  title?: string;
  className?: string;
}

export interface ReadAfterProps {
  items: ReferenceItem[];
  /** Heading. Default «اقرأ أيضاً». */
  title?: string;
  className?: string;
}

export interface MediaBlockProps {
  /** Full YouTube URL or bare 11-char video id. */
  youtubeUrl: string;
  /** Accessible video title (also the thumbnail alt + play label). */
  title: string;
  /** Override thumbnail. Defaults to YouTube's hqdefault image. */
  thumbnailUrl?: string;
  className?: string;
}

export interface AskRayhanWidgetProps {
  pageType: LibraryPageType;
  pageId: string;
  pageTitle: string;
}

export interface CalculatorBlockProps {
  /**
   * Registry slug of the calculator to embed (Phase 3). The block resolves the
   * definition from `lib/calculators/registry` itself — presentational surface,
   * no data fetching. Renders nothing for an unknown slug.
   */
  slug: string;
  className?: string;
}

export interface LibraryPageShellProps {
  children: ReactNode;
  /**
   * `doc` → narrow reading column (max-w-3xl).
   * `hub` → wide directory grid column (max-w-6xl).
   * Default `doc`.
   */
  maxWidth?: "doc" | "hub";
  /** Show the «جرّب ريحان مجاناً» conversion block above the footer. Default true. */
  showCta?: boolean;
}
