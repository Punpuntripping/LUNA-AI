// The ONE dynamic segment under `/blog` — `app/blog/[slug]/` — resolves three
// different vocabularies, and this module owns the string work for all of them.
//
// ⚠ DECODE EXACTLY ONCE, HERE. Next hands a non-ASCII dynamic param to the page
// percent-encoded (`%D8%A7%D9%84…`), so a raw `params.slug` never equals a blog
// slug on its own. `normalizeBlogRef` is the single decode; everything
// downstream — the vocabulary checks, the backend fetches, the canonical URLs —
// takes its output. Decoding twice mangles a slug that legitimately contains a
// `%`; encoding something that arrived encoded produces `%25D8…`, which 404s.
// `lib/library/courts.ts` carries the same rule for the Arabic court slugs.
//
// ⚠ Do NOT copy `lib/library/entities.ts`'s "no normalizer needed" note here.
// That vocabulary is ASCII, so nothing about it can arrive encoded. Half of
// THIS segment's address space is Arabic.
//
// THE THREE VOCABULARIES (`.claude/plans/blog_subjects.md` §2 + §3, revised by
// migration 164):
//
//   subject slug  ASCII kebab-case   `blog_subjects_slug_ascii`
//   blog slug     English kebab-case (set at publish) OR Arabic (minted at
//                 submit) — never 32-hex, never a subject's VALUE
//   legacy token  32 lowercase hex   minted by `blog_service`; the same shape
//                                    `_BARE_TOKEN_RE` matches server-side
//
// Subject and blog slugs can now SHARE a shape, so shape alone no longer picks
// the table. What keeps the dispatch unambiguous is migration 164's pair of
// triggers: no value is ever held by both vocabularies, so trying subjects
// first and blogs second always lands on the one table that has it. The shape
// tests below still skip lookups that could not possibly match — never a
// lookup that could.
//
// A blog slug can also MOVE (the rewriter's English slug replaces the Arabic
// one at «نشر»). The old slug is kept as an alias; the backend resolves it to
// the blog and reports the CURRENT slug, and the page 308s to it.

/**
 * The ASCII kebab-case shape a SUBJECT slug has, mirroring migration 154's
 * `blog_subjects_slug_ascii` CHECK verbatim. Anchored; no `g` flag (a global
 * regex carries `lastIndex` across `.test()` calls).
 */
const SUBJECT_SLUG_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/**
 * A legacy `blog_posts` share token: exactly 32 lowercase hex characters.
 * Mirrors `blog_service._BARE_TOKEN_RE`. 99 of these are in the wild on
 * WhatsApp and Telegram and must keep resolving forever (plan D7).
 */
const LEGACY_TOKEN_RE = /^[0-9a-f]{32}$/;

/**
 * Segments under `/blog` that are pages in their own right and must never be
 * read as a slug. `subjects` is a LITERAL static route (`app/blog/subjects/`),
 * which Next always matches before `[slug]`, so the dispatcher never actually
 * receives it — the set exists so a future reserved word is refused by the
 * reader too, and so the publish path has one list to refuse at mint time
 * (plan §3, the `compliance_entity_sections` lesson).
 */
export const RESERVED_BLOG_SLUGS = new Set<string>(["subjects"]);

/**
 * The one decode. Percent-decodes a `/blog/[slug]` param exactly once and
 * trims; returns the input unchanged when it is not valid percent-encoding
 * (`decodeURIComponent` throws a `URIError` on a lone `%`).
 */
export function normalizeBlogRef(raw: string): string {
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    decoded = raw;
  }
  return decoded.trim();
}

/** Could this ref be a `blog_subjects.slug`? (ASCII kebab-case, migration 154.) */
export function isSubjectSlugShape(ref: string): boolean {
  return SUBJECT_SLUG_RE.test(ref);
}

/**
 * Could this ref be a `public_blogs.slug` (current or former)? Migration 164's
 * CHECK: anything but a 32-hex token and the reserved segments — pure ASCII
 * must additionally be kebab-case.
 */
export function isBlogSlugShape(ref: string): boolean {
  if (ref.length === 0 || RESERVED_BLOG_SLUGS.has(ref)) return false;
  if (LEGACY_TOKEN_RE.test(ref)) return false;
  // Any non-ASCII character makes it an Arabic slug.
  return /[^\u0000-\u007f]/.test(ref) || SUBJECT_SLUG_RE.test(ref);
}

/** Is this ref a legacy `blog_posts` share token? (32 lowercase hex.) */
export function isLegacyTokenShape(ref: string): boolean {
  return LEGACY_TOKEN_RE.test(ref);
}

/**
 * `/blog/{slug}` for a link. The slug is passed RAW: `next/link` encodes the
 * href it renders, and hand-encoding first would double-encode the Arabic.
 * This is the established pattern for Arabic document slugs (`JudgmentCard`
 * links `/judgments/${item.slug}`).
 */
export function blogPath(slug: string): string {
  return `/blog/${slug}`;
}

/** `/blog/{subject}` for a link. ASCII slugs — nothing to encode either way. */
export function subjectPath(slug: string): string {
  return `/blog/${slug}`;
}

/**
 * The ABSOLUTE, encoded form for `alternates.canonical`, OG `url` and JSON-LD.
 * Metadata values are emitted verbatim into the HTML rather than passed through
 * a router, so the encode has to happen here — the same split
 * `/compliance/{slug}` makes between its hrefs and its canonical.
 */
export function blogCanonicalPath(slug: string): string {
  return `/blog/${encodeURIComponent(slug)}`;
}
