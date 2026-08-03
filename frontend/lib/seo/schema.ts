// Typed builder functions for schema.org JSON-LD objects. Each returns a plain
// serializable object (no classes, no dates) so it can be handed straight to
// `<JsonLd data={...} />`. Keep everything Arabic-first where user-visible.
//
// Reference: https://schema.org · Google structured-data docs. The paywall
// fragment (`buildPaywallFragment`) is spread into Article / Legislation to
// mark server-truncated gated bodies as `isAccessibleForFree: false` — the
// approved, non-cloaking way to gate content that still ranks (Phase 1+).

/** A serializable JSON-LD node. */
export type JsonLdObject = Record<string, unknown>;

const SITE_URL = "https://rayhanai.com";
const ORG_NAME = "ريحان";
const ORG_ALT_NAME = "Rayhan";
const LOGO_URL = `${SITE_URL}/icon.png`;

/** Publisher/brand entity — rendered once site-wide from the root layout. */
export function buildOrganization(): JsonLdObject {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": `${SITE_URL}/#organization`,
    name: ORG_NAME,
    alternateName: ORG_ALT_NAME,
    url: SITE_URL,
    logo: {
      "@type": "ImageObject",
      url: LOGO_URL,
    },
    description:
      "المساعد القانوني الذكي للأنظمة السعودية — بحث موثّق في الأنظمة والأحكام القضائية والتعاميم التنظيمية.",
  };
}

/** WebSite entity — enables sitelinks + names the brand. Site-wide. */
export function buildWebSite(): JsonLdObject {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": `${SITE_URL}/#website`,
    name: ORG_NAME,
    alternateName: ORG_ALT_NAME,
    url: SITE_URL,
    inLanguage: "ar",
    publisher: { "@id": `${SITE_URL}/#organization` },
  };
}

export interface ArticleInput {
  title: string;
  description: string;
  url: string;
  datePublished: string;
  dateModified?: string;
  /** Optional hero/OG image absolute URL. */
  image?: string;
}

/** Article node for blog posts and (later) long-form library pages. */
export function buildArticle(input: ArticleInput): JsonLdObject {
  const article: JsonLdObject = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: input.title,
    description: input.description,
    inLanguage: "ar",
    mainEntityOfPage: { "@type": "WebPage", "@id": input.url },
    url: input.url,
    datePublished: input.datePublished,
    dateModified: input.dateModified ?? input.datePublished,
    author: { "@id": `${SITE_URL}/#organization` },
    publisher: { "@id": `${SITE_URL}/#organization` },
  };
  if (input.image) {
    article.image = input.image;
  }
  return article;
}

export interface BreadcrumbItem {
  name: string;
  /** Absolute or site-relative URL of the crumb. */
  url: string;
}

/** Breadcrumb trail — every library/hub page gets one for rich results. */
export function buildBreadcrumbList(items: BreadcrumbItem[]): JsonLdObject {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: item.url.startsWith("http") ? item.url : `${SITE_URL}${item.url}`,
    })),
  };
}

export interface FaqQa {
  question: string;
  answer: string;
}

/** FAQPage node — reg/مادة/form pages carry 4–6 Q&As. */
export function buildFaqPage(qas: FaqQa[]): JsonLdObject {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    inLanguage: "ar",
    mainEntity: qas.map((qa) => ({
      "@type": "Question",
      name: qa.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: qa.answer,
      },
    })),
  };
}

export interface LegislationInput {
  name: string;
  /** Official identifier (رقم المرسوم / decree number). */
  legislationIdentifier?: string;
  /** ISO date the legislation was enacted/issued. */
  legislationDate?: string;
  url: string;
  description?: string;
}

/** Legislation node — the /regulations/{slug} document pages. */
export function buildLegislation(input: LegislationInput): JsonLdObject {
  const legislation: JsonLdObject = {
    "@context": "https://schema.org",
    "@type": "Legislation",
    inLanguage: "ar",
    name: input.name,
    url: input.url,
    mainEntityOfPage: { "@type": "WebPage", "@id": input.url },
    publisher: { "@id": `${SITE_URL}/#organization` },
  };
  if (input.legislationIdentifier)
    legislation.legislationIdentifier = input.legislationIdentifier;
  if (input.legislationDate)
    legislation.legislationDate = input.legislationDate;
  if (input.description) legislation.description = input.description;
  return legislation;
}

/**
 * Paywall fragment to SPREAD into an Article / Legislation node when the body
 * is server-truncated for anon users. `cssSelector` targets the gated DOM
 * region. This is Google's sanctioned subscription-content markup — the same
 * page renders for users and Googlebot (no cloaking), the gate is honest.
 *
 *   { ...buildArticle(...), ...buildPaywallFragment(".gated-body") }
 */
export function buildPaywallFragment(cssSelector: string): JsonLdObject {
  return {
    isAccessibleForFree: false,
    hasPart: {
      "@type": "WebPageElement",
      isAccessibleForFree: false,
      cssSelector,
    },
  };
}
