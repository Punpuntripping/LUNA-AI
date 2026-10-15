// Reference card / dialog labels and exits — pure helpers moved verbatim from
// components/workspace/ReferencePanel.tsx (which imports and re-exports them),
// so the mobile reader labels and links a reference exactly like the web.

import type { Reference, ReferenceDomain, SourceView } from "./references";

/** Domains whose rows carry `regulations_v2.doc_type_raw`. */
const DOC_TYPE_DOMAINS: ReadonlySet<ReferenceDomain> = new Set<ReferenceDomain>([
  "regulations",
  "regulation_docs",
]);

/**
 * The corpus's "we could not determine a type" bucket. A sentinel, not a
 * document type — printing it as the card's chip reads as a broken row.
 */
const DOC_TYPE_UNKNOWN = "غير محدد";

/** `doc_type_raw` when it is a real type, else "" (absent, blank, sentinel). */
export function usableDocType(ref: Reference): string {
  if (!DOC_TYPE_DOMAINS.has(ref.domain)) return "";
  const raw = (ref.doc_type ?? "").trim();
  return raw && raw !== DOC_TYPE_UNKNOWN ? raw : "";
}

/**
 * «WI-9» → ``9``. Anything else → ``null``.
 *
 * The writer publisher writes exactly this form (``agents/writer/publisher.py``),
 * so a strict pattern is the honest test: an alias we cannot parse is an alias we
 * cannot navigate to, and the badge must stay plain text rather than become a
 * button that goes nowhere.
 */
export function parseWiSeq(alias: string | null | undefined): number | null {
  if (!alias) return null;
  const match = /^WI-(\d+)$/.exec(alias.trim());
  if (!match) return null;
  const seq = Number(match[1]);
  return Number.isSafeInteger(seq) ? seq : null;
}

/** Best human-readable title for a reference card. */
export function referenceLabel(ref: Reference): string {
  if (ref.title) return ref.title;
  if (ref.domain === "cases") {
    return [ref.entity_name, ref.regulation_title].filter(Boolean).join(" — ") || "قضية";
  }
  if (ref.domain === "articles") {
    // A titleless مادة would otherwise fall through to its PARENT نظام's name
    // and render as if the card were the whole document. Rebuild the unit —
    // «المادة 81 من نظام العمل» — from the two columns that carry it.
    const num = (ref.article_num ?? "").trim();
    const parent = (ref.regulation_title ?? "").trim();
    if (num) return parent ? `المادة ${num} من ${parent}` : `المادة ${num}`;
    return parent || ref.ref_id || "مادة";
  }
  return ref.regulation_title || ref.ref_id || "مرجع";
}

/**
 * The label a reference gets when it is COPIED, as opposed to rendered.
 *
 * A card and a clipboard answer different questions. On screen the reader has
 * the panel around them — the type chip, the snippet, «عرض المصدر» — so the
 * card's title only has to tell this reference apart from the one under it, and
 * `referenceLabel` is right to be a bare subject line. Pasted into a مذكرة the
 * list is ALONE, and the question becomes "how would someone find this?" —
 * which a subject sentence cannot answer for a ruling and a bare service name
 * cannot answer for a procedure.
 *
 * So each wing contributes the fields that LOCATE its document, joined on one
 * line so the `n-` numbering keeps working:
 *
 *   regulations / articles  «نظام العمل»                     (title alone)
 *   cases        «434939 — 1443 — وزارة العدل»               (رقم / سنة / جهة)
 *   compliance   «إصدار رخصة — وزارة التجارة»                (خدمة / مقدّمها)
 *   circulars    «تعميم بشأن مهلة السداد — البنك المركزي»    (عنوان / جهة)
 *
 * A نظام is deliberately left as-is: its title IS its citation.
 *
 * ⚠ CIRCULARS CARRY NO NUMBER COLUMN. `circulars.circ_ref` looks like one and is
 * not — it is the ingest handle (`18308_circ_4950`, `{entity}_circ_{n}`), which
 * would paste into a memo as a string that identifies nothing and reads as a
 * bug. The real number, where the issuing entity assigned one, is already inside
 * `title` («تعميم رقم 2/2/ت — جنسية وجوازات» — 420 of 1,843 rows), so it arrives
 * for free and must not be prefixed a second time.
 *
 * Every part is optional-safe on purpose: blog `references_json` snapshots are
 * FROZEN at publish time, so a post published before the case fields shipped has
 * neither, and the filter below degrades it to exactly the label it copies today.
 */
/**
 * `«{title} — {entity}»`, unless the title already says the entity.
 *
 * Both corpora embed the issuing body in the document's own name often enough
 * that a blind append is the common case, not the edge one — see the service
 * numbers at the call site. Substring, not equality: the name that swallows the
 * entity usually PREFIXES it rather than equalling it.
 */
function appendOnce(title: string, entity: string | undefined): string {
  const tail = (entity ?? "").trim();
  if (!tail || title.includes(tail)) return title;
  return `${title} — ${tail}`;
}

export function referenceCopyLabel(ref: Reference): string {
  const title = referenceLabel(ref);

  if (ref.domain === "cases") {
    // Number and year first, entity last — the reading order of a citation.
    const number = (ref.case_number ?? "").trim();
    const year = (ref.hijri_year ?? "").trim();
    const entity = (ref.entity_name ?? "").trim();
    // The entity ALONE does not identify a ruling — «وزارة العدل» names 20,671
    // of them. With no number and no year the subject title is the more useful
    // line, so fall back to it rather than emit a court name on its own.
    if (!number && !year) return title;
    return [number, year, entity].filter(Boolean).join(" — ");
  }

  if (ref.domain === "compliance") {
    // `regulation_title` is where the compliance mapping parks
    // `services.provider_name` — the panel's "parent label" slot. Not
    // `entity_name`, which that mapping leaves empty for this domain.
    //
    // ⚠ `services.service_name_ar` ALREADY OPENS WITH THE PROVIDER on 4,944 of
    // 4,985 rows («وزارة العدل - الحاسبة العمالية»), so appending it there
    // yields «… الحاسبة العمالية — وزارة العدل» — the ministry twice in one
    // line. The corpus is the reason this is a containment test and not the
    // equality test it started as: equality catches none of those 4,944.
    // Appending is for the 41 rows whose name stands alone.
    return appendOnce(title, ref.regulation_title);
  }

  if (ref.domain === "circulars") {
    return appendOnce(title, ref.entity_name);
  }

  return title;
}

/**
 * Fallback external URL, read off the revealed source view.
 *
 * Only consulted when the reference row itself carries no URL — the row is the
 * authority, so the card and the dialog can never point at different places.
 */
export function sourceViewExternalUrl(view: SourceView): string {
  switch (view.source_type) {
    case "chunk":
      return view.regulation_source_url || "";
    case "case":
      return view.details_url || "";
    case "gov_service":
      // `service_url` only. The المنصة الوطنية portal link is no longer on the
      // view, and was never the cited document — see the SourceView type.
      return view.service_url || "";
    case "circular":
      return view.url || "";
    case "article_full":
      // A مادة has no official page of its own — the issuing body publishes the
      // نظام. The parent's landing page IS the مادة's official source.
      return view.regulation_source_url || "";
    case "regulation_summary":
      return view.regulation_source_url || "";
    default:
      // Legacy views only (article / section / regulation). ⚠ Falling here is
      // how a new variant silently loses its «فتح المصدر الرسمي» button — no
      // error, just a missing exit.
      return "";
  }
}

/**
 * Arabic doc type WITH the definite article, for «فتح ال… في ريحان».
 *
 * `regulations_v2.doc_type_raw` is a closed 21-value corpus vocabulary, so this
 * is a lookup rather than morphology: prefixing «ال» programmatically mangles
 * every multi-word entry («تقرير/وثيقة» → «التقرير/الوثيقة», not
 * «التقرير/والثيقة»; «مبادئ وأحكام» takes the article on BOTH words). An
 * unknown single word still gets the naive prefix — a new corpus value should
 * read slightly plain, never broken.
 */
const DEFINITE_DOC_TYPE: Record<string, string> = {
  نظام: "النظام",
  لائحة: "اللائحة",
  "لائحة تنفيذية": "اللائحة التنفيذية",
  "لائحة فنية": "اللائحة الفنية",
  تنظيم: "التنظيم",
  دليل: "الدليل",
  قواعد: "القواعد",
  ضوابط: "الضوابط",
  متطلبات: "المتطلبات",
  "مواصفة قياسية": "المواصفة القياسية",
  إجراءات: "الإجراءات",
  سياسة: "السياسة",
  "جدول/قائمة": "الجدول/القائمة",
  تعليمات: "التعليمات",
  "مبادئ وأحكام": "المبادئ والأحكام",
  اتفاقية: "الاتفاقية",
  "تقرير/وثيقة": "التقرير/الوثيقة",
  "برنامج/خطة": "البرنامج/الخطة",
  ترجمة: "الترجمة",
  "قرار/مرسوم": "القرار/المرسوم",
};

/**
 * Per-domain default when the row carries no usable `doc_type`. EXHAUSTIVE —
 * an omission here would silently render «فتح  في ريحان» with a hole in it.
 *
 * `articles` is «المادة», PINNED by §6.1a: «فتح المادة في ريحان» — and the
 * button lands on the مادة's own `/regulations/{reg}/{article}` page, which the
 * library wing already publishes.
 */
const DEFINITE_DOMAIN_TYPE: Record<ReferenceDomain, string> = {
  regulations: "النظام",
  cases: "الحكم",
  compliance: "الخدمة",
  circulars: "التعميم",
  articles: "المادة",
  regulation_docs: "النظام",
};

export function referenceDefiniteType(ref: Reference): string {
  // `doc_type` belongs to the two regulation-backed domains (a chunk of a
  // document, and the document itself); «غير محدد» is the corpus's "we could
  // not determine one" bucket — a sentinel, not a document type. `articles` is
  // excluded: «فتح المادة في ريحان» stays المادة even under a لائحة.
  const raw = usableDocType(ref);
  if (!raw) return DEFINITE_DOMAIN_TYPE[ref.domain] ?? DEFINITE_DOMAIN_TYPE.regulations;
  const known = DEFINITE_DOC_TYPE[raw];
  if (known) return known;
  return raw.startsWith("ال") ? raw : `ال${raw}`;
}

/**
 * The FULL label on the in-app exit — «فتح النظام في ريحان», «فتح الحكم في
 * ريحان»… and, for a government service, «افتح الدليل الشامل للخدمة في ريحان».
 *
 * Compliance needs the whole string rather than another `DEFINITE_DOMAIN_TYPE`
 * entry because it does not fit the `فتح {X} في ريحان` template at all: the verb
 * changes to «افتح» and the object is «الدليل الشامل للخدمة», not «الخدمة». What
 * the button opens is not the service — it is the SERVICE GUIDE, our own
 * authored rewrite of the entity's official PDF user guide, and naming it
 * «الخدمة» would promise the reader the entity's service page, which is what the
 * OTHER exit («فتح المصدر الرسمي») already is. Two buttons that read as the same
 * destination is the failure this arm avoids.
 *
 * `referenceDefiniteType` is deliberately left returning «الخدمة» for the
 * `compliance` domain: it is also the ملخص label inside `SourceViewContent`,
 * where «الخدمة» is right and the guide has no business appearing.
 *
 * The two callers — the card and the reveal dialog — share this so the same
 * reference cannot be labelled two ways on two surfaces.
 */
export function referenceLibraryExitLabel(ref: Reference): string {
  if (ref.domain === "compliance") return "افتح الدليل الشامل للخدمة في ريحان";
  return `فتح ${referenceDefiniteType(ref)} في ريحان`;
}

/** The single external URL a card's "فتح المصدر الرسمي" button targets. */
export function referencePrimaryUrl(ref: Reference): string {
  switch (ref.domain) {
    case "regulations":
      return ref.landing_url || "";
    case "compliance":
      // `ref.url` (the المنصة الوطنية portal) is deliberately NOT a fallback:
      // it is a directory, not the service, and a card that offers it is
      // promising a source it does not deliver. No service_url ⇒ no exit.
      return ref.service_url || "";
    case "circulars":
      return ref.url || "";
    case "cases":
      return ref.details_url || "";
    case "regulation_docs":
      // Same column as a chunk ref: `landing_url` is the نظام's page on the
      // issuing body's site, and a `regulation_docs` ref IS that نظام.
      return ref.landing_url || "";
    case "articles":
      // The parent نظام's landing page — a مادة is not published standalone by
      // any issuing body, so this is the honest official exit for it.
      return ref.landing_url || "";
    default:
      // ⚠ A domain that lands here loses «فتح المصدر الرسمي» with no error.
      return "";
  }
}

/**
 * The «فتح المصدر الرسمي» target for a reference: the row's own URL, else the
 * revealed source view's (when one is at hand). "" = no official exit. Same
 * precedence as the web reveal dialog.
 */
export function officialUrl(ref: Reference, view?: SourceView | null): string {
  return referencePrimaryUrl(ref) || (view ? sourceViewExternalUrl(view) : "");
}
