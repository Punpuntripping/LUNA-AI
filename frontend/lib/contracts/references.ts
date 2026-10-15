// Reference (citation) shapes + the metered source reveal — moved verbatim from
// frontend/types/index.ts (the web re-exports them from `@/types`).

// ==========================================
// DEEP_SEARCH ARTIFACT REFERENCES (JSON render object)
// ==========================================
// Mirrors agents/deep_search_v4/aggregator/models.py::Reference and
// agents/deep_search_v4/source_viewer.py::SourceView. Migration 049: refs
// live in the relational `workspace_item_references` table; the backend
// reconstructs this shape on read by joining to source tables. The
// frontend fetches via `useWorkspaceItemReferences(item_id)` and the
// workspace ReferencePanel renders the response.

/**
 * The cited object's WING — the read path keys every shell builder off this.
 *
 * `regulations` means **a chunk** of a نظام (`chunks_v2.id`), which is why the
 * two `simple_search` additions could not reuse it:
 *
 * - `articles`         — one مادة (`articles_v2.id`), `ref_id` = `article:<uuid>`
 * - `regulation_docs`  — a WHOLE نظام (`regulations_v2.id`), `ref_id` =
 *                        `regdoc:<uuid>`
 *
 * ⚠ NEVER give either of them the `reg:` prefix. `domain='regulations'` hard-
 * assumes the id is a `chunks_v2.id`; a regulation/article uuid passes the uuid
 * check, inserts cleanly, then resolves to nothing on read and renders a dead
 * stub with no «عرض المصدر» — zero errors anywhere in the chain.
 *
 * Mirrors the DB CHECK on `workspace_item_references.domain` and the Literal on
 * `agents/deep_search_v4/aggregator/models.py::Reference.domain`. The
 * `Record<ReferenceDomain, …>` tables in `ReferencePanel` are exhaustive on
 * purpose: adding a member here must not compile until the panel knows it.
 */
export type ReferenceDomain =
  | 'regulations'
  | 'compliance'
  | 'cases'
  | 'circulars'
  | 'articles'
  | 'regulation_docs';

export type ReferenceSourceType =
  | 'article'
  | 'section'
  | 'chunk'
  | 'regulation'
  | 'gov_service'
  | 'form'
  | 'case'
  | 'circular'
  // simple_search (§6.1). Deliberately NOT `article` / `regulation` — those two
  // names are already taken by the legacy views below, and reusing them gives
  // the backend's Pydantic discriminated union duplicate discriminator values
  // (an import-time crash) and the frontend union a silent wrong-arm match.
  | 'article_full'
  | 'regulation_summary';

/** One resolved cross-reference from a regulation chunk to a target unit. */
export interface CrossRef {
  target_type: string;
  target_reg_title: string;
  target_number: number | null;
  relation: string;
  content: string;
}

/** Click-ready original-source payload — discriminated on `source_type`. */
export type SourceView =
  | {
      source_type: 'chunk';
      title: string;
      content: string;
      /**
       * The same مقطع split into prose runs, rendered tables and rendered
       * FIGURES — the corpus flattened every table into a bulleted list before
       * ingestion and flattened every figure into nothing but its own file
       * path, and this puts the grid and the diagram back for the reader.
       *
       * `content` above stays the PROSE and is still what «نسخ المحتوى» copies:
       * pasting a source into a memo must yield text, not `<table>` markup and
       * not a CDN url.
       *
       * Empty (or absent) means "render `content`, exactly as before" — which
       * is the 82% of chunks with no table, the 96.7% with no figure, every
       * judgment riding this shape, and every artifact persisted before this
       * shipped. (Persisted citations rebuild from the DB on the click, so the
       * 56 that point at raw image markup repair themselves; nothing is
       * backfilled.) Each `html` is sanitized server-side by an allowlist
       * re-serializer.
       */
      display_segments?: (
        | { kind: 'text'; text: string }
        | { kind: 'table'; ref: string; html: string; md: string }
        /**
         * A figure spliced in where the corpus wrote `![img-1.jpeg](images/…)`.
         * Until this shipped, `MarkdownRenderer` turned that span into an
         * `<img src="images/page_005_img_001.jpeg">` — a RELATIVE url against
         * the app origin, so 56 already-written citations rendered a broken
         * image icon inside a cited statute.
         *
         * `n` is the render-order caption number (chunk-scoped in this popup),
         * `description` is the alt, `title` the caption. `weight` / `span_len`
         * are the GATE's arithmetic, settled server-side and never rendered —
         * carried so a caller holding a whole segment can pass it around whole.
         */
        | {
            kind: 'image';
            image_ref: string;
            n: number;
            title: string;
            description: string;
            url: string;
            width: number;
            height: number;
            weight: number;
            span_len: number;
          }
      )[];
      regulation_title: string;
      /**
       * Parent regulation's landing page — the ONE external exit. The PDF
       * companion was removed with the reference redesign (2026-08-01): the
       * source popup offers exactly two ways out, the official link and the
       * item's page in our own library.
       */
      regulation_source_url: string;
    }
  | {
      source_type: 'case';
      title: string;
      /**
       * The ruling's structured ملخص (`cases.summary`, falling back to
       * `short_summary`) — markdown, and what the popup RENDERS. The raw
       * judgment text is deliberately not here: «فتح الحكم في ريحان» opens
       * `/judgments/{slug}`, and the reveal already paid the unlock for that
       * same document, so the full text costs nothing extra to reach.
       */
      summary: string;
      /**
       * Raw ruling text (markdown) — a LAST-RESORT body, non-empty only for the
       * handful of rulings that carry no summary. Never both this and `summary`.
       */
      content: string;
      /**
       * The ruling's own page on the issuing body's site — وزارة العدل only (20,671
       * of 30,531 rulings). The rest were parsed out of PDFs and reach their source
       * through `official_sources`. This stays the target of «فتح المصدر الرسمي».
       */
      details_url: string;
      /**
       * «المجلد» / «الصفحات» — which bound مجلد this ruling was lifted out of and at
       * which pages. Present for the 5,538 volume-parsed rulings, empty for a وزارة
       * العدل ruling (it has its own page) and for a standalone قرار PDF (one file,
       * one decision — a page range would describe the file, not locate anything).
       *
       * OPTIONAL: artifacts persisted before 2026-08-19 have no such key, and their
       * dialogs simply render no citation block.
       */
      citation?: { label: string; value: string }[];
      /**
       * The publisher's collection page and the volume/decision PDF, for the 9,860
       * rulings with no `details_url` of their own. This is what lets «عرض المصدر»
       * answer "where is this from?" for a قرار زكوي or a ديوان المظالم ruling —
       * before it, those revealed a body with no attribution at all.
       *
       * Safe to render here even though the /judgments page withholds the same links
       * from anonymous readers: reaching this view already spent the item's unlock.
       */
      official_sources?: { title: string; href: string }[];
    }
  | {
      /**
       * A government service — title and link, NO body, and that is still true
       * (2026-08-19). The popup used to restate the service's intro, الخطوات,
       * المتطلبات and المستندات المطلوبة. That content is gone and stays gone: a
       * procedure goes stale when its issuing entity edits it, and repeating the
       * entity's own text under our chrome makes us look like the authority on a
       * process we don't own. The service's own page is the authority.
       *
       * What DID come back is the `/compliance` wing, with different content:
       * **service guides**, our own authored rewrite of the issuing entity's
       * official PDF user guide, published in full and ungated at
       * `/compliance/{slug}`. ~169 of 4,746 services have one. That changes the
       * dialog's EXITS, not this view — when a guide exists the panel gains a
       * second button («افتح الدليل الشامل للخدمة في ريحان») and the reader
       * leaves for the guide's own page. No guide content is ever inlined here,
       * and none of it enters agent context.
       */
      source_type: 'gov_service';
      title: string;
      /**
       * `services.service_url` — the OFFICIAL exit, and still the only outbound
       * one: the guide never links the source PDF, and the المنصة الوطنية portal
       * link (`national_platform_url`) went with the body, because a portal home
       * page is not the cited source. May be "" when the corpus has no link, in
       * which case the popup shows the title alone (plus the guide exit, if the
       * backend resolved one).
       */
      service_url: string;
    }
  | {
      source_type: 'circular';
      title: string;
      /** Issuing authority name (``entities.name``). */
      entity_name: string;
      /**
       * FULL circular body (markdown) — uncapped. Can be extremely long
       * (up to ~168k chars for outliers); the source-view dialog scrolls it
       * inside its own ``overflow-y-auto`` container rather than growing the
       * layout.
       */
      content: string;
      /** Circular reference id (``circulars.circ_ref``). */
      circ_ref: string;
      /** Optional external source link (``circulars.source``); may be "". */
      url: string;
    }
  // ---- simple_search (§6.1a) ----------------------------------------------
  // ⚠ These MUST stay ABOVE the legacy arm. That arm is a permissive
  // ``[k: string]: unknown`` bag, so a variant whose ``source_type`` overlapped
  // it would be absorbed with NO compile error and would render through
  // ``SourceViewContent``'s fall-through as bare markdown — looking like it
  // works. The distinct ``article_full`` / ``regulation_summary`` discriminators
  // are what keep the narrowing honest; never rename them to
  // ``article`` / ``regulation``.
  | {
      /**
       * ONE مادة, served whole — ``articles_v2.content`` verbatim, never a chunk
       * slice. This is simple_search's L3 leg, and the one behavioural
       * difference from deep_search, where a fetched article is text-only and
       * never becomes a citation.
       */
      source_type: 'article_full';
      /** «المادة 81 من نظام العمل». */
      title: string;
      /**
       * ``articles_v2.article_number`` — **text**, not a number: the corpus
       * carries forms like «81 مكرر» that no integer column can hold. Rendered
       * verbatim, in Western digits (§6.4's digit rule).
       */
      article_num: string | null;
      /**
       * The FULL article body (markdown). Named ``content`` on purpose:
       * ``extractSourceContent`` does ``"content" in view ? view.content : ""``,
       * so any other field name silently yields a blank dialog with no copy
       * button.
       */
      content: string;
      /** Parent نظام's title, for the dialog's «نص المادة … — …» header line. */
      regulation_title: string;
      /** Parent ``regulations_v2.landing_url`` — the ONE external exit. */
      regulation_source_url: string;
    }
  | {
      /**
       * A WHOLE نظام, represented by its SUMMARY. The body here is
       * ``regulations_v2.llm_summary`` (falling back to ``summary``) —
       * deliberately NOT the statute's full text, which runs past 1.1M chars for
       * outliers and belongs on the library page this dialog links to. The popup
       * frames it as a ملخص so it is never mistaken for the نظام itself.
       */
      source_type: 'regulation_summary';
      /** ``clean_title`` or ``title``. */
      title: string;
      /** The regulation's summary (markdown). See ``article_full.content``. */
      content: string;
      /** ``regulations_v2.landing_url`` — the ONE external exit. */
      regulation_source_url: string;
    }
  // Legacy variants — retained for reload of pre-URA-v3.0 artifacts.
  | {
      source_type: 'article' | 'section' | 'regulation';
      title: string;
      content?: string;
      [k: string]: unknown;
    };

/** One numbered citation entry in a deep_search artifact's reference list. */
export interface Reference {
  n: number;
  source_type: ReferenceSourceType;
  domain: ReferenceDomain;
  relevance: 'high' | 'medium';
  regulation_title: string;
  title: string;
  snippet: string;
  ref_id: string;
  article_num?: string | null;
  section_title?: string | null;
  landing_url: string;
  service_url: string;
  url: string;
  details_url: string;
  entity_name: string;
  /**
   * `cases.case_number` (falling back to `judgment_number`) — the ruling's own
   * number, and `cases.date_hijri` already reduced to its bare 4-digit year by
   * the backend's `judgment_naming.hijri_year`.
   *
   * NEITHER IS RENDERED on the card: its title is the ruling's SUBJECT, which
   * is what tells two judgments apart while reading. These exist for
   * `referenceCopyLabel` — a subject sentence does not LOCATE a ruling, and
   * «434939 — 1443 — وزارة العدل» is what a pasted مذكرة needs.
   *
   * Optional because a blog snapshot frozen into `references_json` before these
   * shipped has neither key; the copy label falls back to the title there. The
   * year is also absent for the 3,552 rulings whose date does not parse.
   * `cases` domain only.
   */
  case_number?: string;
  hijri_year?: string;
  /**
   * `regulations_v2.doc_type_raw` — the document's own Arabic type
   * (لائحة / تنظيم / دليل / مواصفة قياسية / …). Rendered as the card's type
   * chip in place of the blanket نظام label. Empty string (or absent, on
   * pre-existing snapshots) when the corpus has no determined type — the
   * panel then falls back to the domain's own label. «غير محدد» is the
   * corpus's "could not determine" sentinel and is treated as absent.
   *
   * Carried by the two regulation-backed domains only: `regulations` (a chunk
   * of the document) and `regulation_docs` (the whole document). NOT by
   * `articles` — a مادة is «المادة» whatever its parent's doc type is.
   */
  doc_type?: string;
  /**
   * «الإحالات» — the citations this source points at, rendered inside the
   * source-reveal dialog under the body.
   *
   * Populated for BOTH regulations (projected from `cross_references_v2`) and
   * cases (a ruling's `referenced_regulations`, normalised onto this same shape
   * by `preprocessor.case_ref_to_cross_ref`) — one shape, one renderer. Empty
   * for compliance / circulars, which have no citation mesh.
   */
  cross_refs: CrossRef[];
  /**
   * ACCESS-TIERS PHASE C (§6.2 step 1): **always `null` on the list payload.**
   * Source bodies left the references list — the full body is metered content
   * now and is fetched one item at a time from
   * ``GET /workspace/{item_id}/references/{n}/source`` after ``resolve_access``.
   * The key is retained on the wire so an un-migrated client degrades to "no
   * reveal button" instead of crashing on a missing property.
   *
   * ⚠ NEVER branch on this to decide whether a source exists — branch on
   * ``has_source``. This field only ever carries a body for a caller that
   * explicitly asked the backend for one (none do today).
   */
  source_view: SourceView | null;
  /**
   * ACCESS-TIERS PHASE C: a full source view CAN be built for this ``n``.
   *
   * THIS is what decides whether «عرض المصدر» renders and whether a ``[n]``
   * click opens the reveal dialog. It costs no request to learn (a probe would
   * be either a charge or a free oracle), and it is false for references whose
   * source row could not be reconstructed (stub cards) — and for every
   * reference on an anonymous blog snapshot, whose frozen bodies the backend
   * strips on read.
   */
  has_source: boolean;
  /**
   * The cited item's page in OUR library («فتح الحكم في ريحان»). `null` when
   * the item has no published page — the card then renders the external link
   * alone, never a hub fallback. Absent on pre-existing blog snapshots.
   *
   * NAVIGATION, NOT CONTENT: this link is free and unmetered. The library page
   * enforces its own access tier; metering the link too would double-charge.
   * Compliance references never carry one — `/compliance` was retired and has
   * no wing to point at.
   */
  library_url?: string | null;
  /**
   * Writer-publisher attribution: when this reference was projected onto an
   * ``agent_writing`` workspace item from a source research WI, ``source_wi``
   * carries the LLM-facing alias (e.g. ``"WI-1"``) of that source. Lives
   * exclusively in ``workspace_items.metadata.references`` on the writer's
   * output row — NOT in ``workspace_item_references`` rows — so callers that
   * fetch references via ``/workspace/{id}/references`` must overlay it from
   * the item's metadata blob to surface provenance to the lawyer.
   *
   * Always undefined for ``agent_search`` items (no source disambiguation
   * applies — the search agent IS the source).
   */
  source_wi?: string | null;
  /**
   * Writer-publisher attribution: the ``n`` this ref had inside the source WI
   * before the writer renumbered it 1..K in body order. Useful for forensic
   * click-through ("which (n) on WI-1 produced this card?"); same overlay
   * rules as ``source_wi``.
   */
  source_n?: number | null;
}

/**
 * Entry shape inside ``workspace_items.metadata.references`` for an
 * ``agent_writing`` item. Written by ``agents.writer.publisher`` to give the
 * frontend a thin attribution view that maps the writer's body-order ``n``
 * back to (source_wi alias, source ref n, ref_id, domain). The full Reference
 * payload (title, snippet, source_view, …) still lives in the relational
 * ``workspace_item_references`` table and is reconstructed by the existing
 * ``/workspace/{id}/references`` endpoint.
 */
export interface WriterMetadataReferenceView {
  n: number;
  source_wi: string | null;
  source_n: number;
  ref_id: string;
  domain: ReferenceDomain;
}

// ==========================================
// ON-DEMAND REFERENCE SOURCE REVEAL (access-tiers Phase C, §6.2)
// ==========================================
// GET /api/v1/workspace/{item_id}/references/{n}/source
//   authed · `Cache-Control: private, no-store` · 20/min per verified caller
//   (one shared budget with `/library/full/*`, so a 429 is reachable).
//
//   200 → { n, ref_id, domain, source_view, unlocked, balance }
//   402 → the D14 refusal body (parsed by `parseRefusal`, rendered by
//         `refusalCardCopy` — both in lib/library/*). NO content bytes.
//   404 → «العنصر غير موجود» / «المرجع غير موجود» / «تعذّر عرض هذا المصدر».
//         The last one is a corpus gap, not a refusal — the unlock (if one was
//         spent) is permanent, so a later retry costs nothing.

/**
 * Why the reveal succeeded. Only `granted` actually spent anything:
 * - `granted`          — a ledger row was just written; `cost` was charged.
 * - `already_unlocked` — the item was on the shelf already (free, forever).
 * - `open`             — policy-open item (compliance service, short تعميم);
 *                        the quota was never consulted, so `balance` is null.
 */
export type ReferenceUnlockReason = 'granted' | 'already_unlocked' | 'open';

/**
 * WHAT was unlocked — not what was clicked (D15.1).
 *
 * ~81% of `reg:` citations resolve to the whole نظام rather than the single
 * chunk the lawyer clicked, because only 2,140 of 11,455 chunks own exactly one
 * مادة. The unlock genuinely covers the entire statute (and every مادة under
 * it, per D5), so the UI must say so: a reader who thinks they spent an unlock
 * on one paragraph has been tricked by the interface, not by the meter.
 */
export interface ReferenceUnlockInfo {
  /** `regulation` | `article` | `judgment` | `circular` | `service` | … */
  content_type: string;
  content_id: string;
  /** Human title of the unlocked item — the نظام, never the chunk. */
  title: string;
  /**
   * Non-null only when the chunk owned exactly one مادة.
   *
   * ⚠ STRING, not number (§12a C4). `articles_v2.article_number` is TEXT and
   * carries «1-1» and «81 مكرر» — 487 of 51,792 rows. Typed `number` the
   * backend had to send `null` for exactly those, and the notice silently fell
   * back to naming the whole نظام *after* the reader spent an unlock. Pinned
   * `string | null` at every hop; render it with `arDigits`, never `arNumber`
   * (which rounds).
   */
  article_no: string | null;
  charged: boolean;
  /** Weighted cost (§1.2.1): 1 for most items, up to 8 for a large نظام. */
  cost: number;
  reason: ReferenceUnlockReason;
}

/**
 * The «فتح المصادر» allowance AFTER this reveal (a `granted` decision reports
 * the post-charge number). `null` on the response — not here — means the quota
 * was never consulted; `limit: null` means unlimited. Those are different, and
 * conflating them shows «0 متبقٍ» to a dev account.
 */
export interface ReferenceBalance {
  used: number;
  /** `null` = unlimited. */
  limit: number | null;
  resets_at: string | null;
}

/** 200 body of the metered reveal. */
export interface ReferenceSourceResponse {
  n: number;
  ref_id: string;
  /** `ReferenceDomain` on every real row; typed loosely — it is display-inert. */
  domain: string;
  source_view: SourceView;
  unlocked: ReferenceUnlockInfo;
  /** `null` for a policy-open item — the meter was never consulted. */
  balance: ReferenceBalance | null;
  /**
   * The cited item's page in OUR library — `/regulations/{slug}`,
   * `/judgments/{slug}`, `/circulars/{slug}` or `/compliance/{slug}` — for the
   * «فتح ... في ريحان» button beside the official link.
   *
   * `null` means the item has NO published page (no `seo_item_meta` slug yet),
   * and the button must then be dropped entirely. Never substitute the wing hub:
   * a button that promises the document and delivers a list is worse than one
   * that isn't there.
   *
   * A مادة-level citation resolves to its **نظام** page, not to
   * `/regulations/{reg}/{article}` — the chunk behind a citation is an arbitrary
   * slice and 81% of them already lift to the whole statute, so the button
   * always lands in the same place.
   */
  library_url: string | null;
}

/** Why a reveal produced no source, when it was NOT an entitlement refusal. */
export type ReferenceSourceError =
  /** No in-memory access token (session never hydrated). */
  | 'no_token'
  /** 401/403 — the session died. Card + login CTA, never a forced redirect. */
  | 'unauthorized'
  /** 404 — unknown item/ref, or the corpus row is gone. */
  | 'not_found'
  /** 429 — the shared 20/min library budget. NOT a quota refusal. */
  | 'rate_limited'
  /** Network failure / unparsable body. */
  | 'network'
  /** 5xx or any other unexpected status. */
  | 'server';
