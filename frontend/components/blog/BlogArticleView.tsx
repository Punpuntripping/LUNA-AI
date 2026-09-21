"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { SubjectChips } from "@/components/blog/SubjectChips";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
// Imported from their own modules, NOT the `blocks` barrel: this is a client
// component, so a barrel import would drag every other block (the page
// shells, the guide body, the related strip) into the browser bundle with it.
import { AskRayhanWidget } from "@/components/library/blocks/AskRayhanWidget";
import { ChatWithPageCta } from "@/components/library/blocks/ChatWithPageCta";
import { TocFloating } from "@/components/library/blocks/TocFloating";
import { TocList } from "@/components/library/blocks/TocList";
import { TocRail } from "@/components/library/blocks/TocRail";
import { AgentOutputDisclaimer } from "@/components/workspace/AgentOutputDisclaimer";
import {
  ReferencePanel,
  referenceCopyLabel,
} from "@/components/workspace/ReferencePanel";
import { Button } from "@/components/ui/button";
import { extractHeadings } from "@/lib/markdown/headings";
import { stripMarkdownImages } from "@/lib/markdown/images";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import type { BlogSubjectRef, Reference } from "@/types";
import type { TocEntry } from "@/types/library";

// Subtype → Arabic kicker label. Mirrors PublicAnswerView / WorkspaceCard so
// the editorial page speaks the same vocabulary as the in-app workspace.
// LEGACY ONLY: a `blog_posts` row carries a `subtype`; a `public_blogs` row
// carries a `type` instead and renders it through `SubjectChips`.
const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

// The shared library scrollspy watches `#sec-` hrefs by default — the shape a
// corpus document page emits. A blog's anchors are bare `slugifyHeading` ids,
// so the rail rendered with no row ever lighting up: a silent regression against
// `BlogTableOfContents`, which this TOC swap replaced and which had its own
// working IntersectionObserver. Widening the spy is the safe half of the fix —
// `sec-`-prefixing blog heading ids instead would break every `#slug` link
// already copied out of a published article.
//
// ⚠ The OTHER half took a second pass (2026-09-21). Matching the hrefs only got
// the rows observed; the spy still named the wrong one, because it picked the
// first target INTERSECTING its band and a blog's targets are `<h2>` lines
// rather than the `<section>` spans a corpus page emits — so most of the time
// none intersected and the label was whatever it last saw. The fallback now
// lives in `useTocScrollspy`; see the note there.
const TOC_SPY_PREFIX = "#";

// Gregorian Arabic byline date (e.g. «30 يونيو 2026»). No shared date helper
// exists in ``frontend/lib`` yet, so the formatter is built once here.
const BYLINE_DATE_FORMAT = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatBylineDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return BYLINE_DATE_FORMAT.format(d);
}

/**
 * Count pill for the TOC header, e.g. «7 أقسام». Latin digits: a TOC badge is
 * app chrome, not corpus text, so the numerals policy applies with no carve-out.
 * Arabic counts 3–10 with the plural («أقسام») and 11+ with the singular
 * accusative («قسمًا»); this surface never renders fewer than 2 (see `showToc`).
 */
function tocBadge(count: number): string {
  return count <= 10 ? `${count} أقسام` : `${count} قسمًا`;
}

/**
 * The reading surface's view of a blog, satisfied by BOTH shapes this route
 * serves — the legacy `blog_posts` snapshot (`BlogPostPublic`) and the public
 * wing's `public_blogs` row (`PublicBlogDetail`). Structural on purpose: the
 * two payloads differ only in their kicker (`subtype` vs `type` + `subjects`),
 * and a union would push a discriminator into every consumer.
 */
export interface BlogArticlePost {
  title: string | null;
  question_text: string;
  content_md: string;
  references: Reference[];
  created_at: string;
  /** LEGACY `blog_posts` kicker. Absent on the public wing. */
  subtype?: string | null;
  /** `public_blogs.type` — the badge. Absent on the legacy snapshot. */
  type?: string | null;
  /** `public_blogs` subject chips. Absent on the legacy snapshot. */
  subjects?: BlogSubjectRef[];
}

interface BlogArticleViewProps {
  post: BlogArticlePost;
  /**
   * The address of THIS blog for the METERED source reveal — the key
   * `ReferencePanel` hands to `GET /public/blog/{key}/references/{n}/source`.
   *
   * ⚠ ITS SHAPE DEPENDS ON THE SURFACE, which is why it is a prop and is never
   * derived here:
   *   • `/blog/{token}`  (legacy `blog_posts`)  → the 32-hex TOKEN
   *   • `/blogs/{token}` (مدوناتي owner view)   → the 32-hex TOKEN
   *   • `/blog/{slug}`   (`public_blogs`)       → the ARABIC SLUG
   *
   * A reader is not the author, so the workspace reveal endpoint would 404
   * them; for a legacy post the unguessable token IS the capability. A public
   * blog has no token at all (plan D17), so that endpoint becomes slug-keyed
   * for this wing (plan §3). The entitlement rules are unchanged either way —
   * they are evaluated against the READER, and an anonymous one gets the 402
   * «سجّل مجاناً» card rather than a login redirect.
   *
   * ⚠ IT IS ALSO THE `page_id` FOR «اسأل ريحان» AND THE CHAT CARRY, and that is
   * not a coincidence worth undoing: `ask_service._ground_blog` resolves the
   * same two vocabularies by the same shape test, so whatever addresses the
   * article here addresses it there.
   */
  sourceKey: string;
  /**
   * SERVER-RENDERED SLOTS. Both are rendered by the route (a server component)
   * and handed down as elements, because `RelatedStrip` is a server component
   * by contract — importing it here would pull it, `RelatedStripTrack` and every
   * card into this client bundle and quietly break that contract. Passing
   * finished elements through props is what keeps them on the server.
   */
  breadcrumbs?: ReactNode;
  /** «اقرأ تاليًا». Absent on the legacy wing, which has no `public_blogs` row. */
  relatedStrip?: ReactNode;
}

/**
 * The مدونة reading surface: an editorial article with a centered hero title,
 * branded byline, the type badge + subject chips, the library table-of-contents
 * pair («محتويات المدونة»), the article body, and the reference panel.
 *
 * Serves BOTH public blog shapes — a `public_blogs` row and a legacy
 * `blog_posts` title-mode snapshot. Anon-accessible: it receives the frozen
 * content (`content_md` + `references`) as props. The only auth-aware element is
 * the `ChatWithBlogButton` action, which degrades to a login-redirect.
 *
 * THE TOC (plan §4, D8). `BlogTableOfContents` is retired in favour of the
 * library pair, so a blog gets the treatment a corpus document gets: `TocRail`
 * sticky on `lg:` and up, `TocFloating` on phones, and the inline `TocList`
 * above the body — which is both the crawlable copy of the index and the
 * in-flow sentinel `TocFloating` measures its pill against. All three consume
 * the same `TocEntry[]` projected from `extractHeadings`, whose slugs equal the
 * ids `MarkdownRenderer` emits under `headingAnchors`; that shared slugger is
 * the only reason these anchors resolve.
 *
 * ⚠ `parseTocLabel` (inside all three components) is built for «المادة 80» and
 * falls through to `{ chip: null, text: label }` on a prose heading, rendering
 * the full heading with no gutter chip. That fallback IS the correct blog
 * behaviour — do not "fix" it.
 *
 * ⚠ `useTocScrollspy` defaults to tracking `#sec-` hrefs (the library's gated
 * chunk anchors); a blog heading anchor is a bare slug, so this wing passes
 * `spyPrefix="#"`. Both statements this comment used to make after that — «no
 * rail row lights up» and «the phone pill keeps its «المحتويات» fallback label»
 * — described a spy that has since been rewritten: it now resolves the active
 * row from live rects against the reading line, which works the same for a
 * heading anchor as for a section. Clicks, smooth scroll and the missing-anchor
 * fallback are unchanged.
 *
 * المراجع — UNTOUCHED (plan §4). Citation fluidity mirrors PublicAnswerView /
 * AgentSearchViewer EXACTLY: clicking `[n]` in the body sets `focusedN`
 * (re-armed via requestAnimationFrame so repeat clicks on the same N re-fire),
 * which drives ReferencePanel's `focusedReferenceN`; `handleFlashDone` clears
 * it. The references panel is the visible proof-of-work this surface is built
 * around, and the one thing on the page a reader cannot get from a chatbot.
 *
 * The brand header, «جرّب ريحان مجاناً» CTA, and footer come from
 * `BlogPageShell` — they are NOT duplicated here.
 */
export function BlogArticleView({
  post,
  sourceKey,
  breadcrumbs,
  relatedStrip,
}: BlogArticleViewProps) {
  const [focusedN, setFocusedN] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);

  const handleBodyCitationClick = useCallback((n: number) => {
    // Clear first so ReferencePanel's effect fires even on consecutive clicks
    // of the same N (the effect only runs when the value changes).
    setFocusedN(null);
    window.requestAnimationFrame(() => setFocusedN(n));
  }, []);

  const handleFlashDone = useCallback(() => {
    setFocusedN(null);
  }, []);

  const references = useMemo(() => post.references ?? [], [post.references]);
  const subtypeLabel = post.subtype
    ? SUBTYPE_LABEL[post.subtype] ?? post.subtype
    : null;
  const title = (post.title ?? "").trim() || post.question_text;
  const body = post.content_md ?? "";
  const bylineDate = formatBylineDate(post.created_at);

  // `TocHeading[]` → `TocEntry[]`. The gate and the slugs are unchanged from
  // the retired component; only the projection is new.
  const tocEntries = useMemo<TocEntry[]>(
    () =>
      extractHeadings(body).map((heading) => ({
        id: heading.slug,
        label: heading.text,
        href: `#${heading.slug}`,
        level: heading.depth,
      })),
    [body],
  );
  // FEWER THAN 2 HEADINGS ⇒ NO TOC AT ALL, and the body takes the full width.
  // A one-row index is furniture: it costs a sticky column and tells the reader
  // nothing they cannot already see.
  const showToc = tocEntries.length >= 2;
  const badge = tocBadge(tocEntries.length);

  // The `/login?…` target an anon reader's «تحدّث مع ريحان» falls back to.
  // Built the same way `AskRayhanWidget` builds its own so the two agree on the
  // querystring `AskRayhanLoginIntent` reads back; the `chat_with_library_item`
  // intent the button stashes is what actually resumes the carry after sign-in.
  const loginHref = useMemo(() => {
    const params = new URLSearchParams({
      intent: "ask_rayhan",
      page_type: "blog",
      page_id: sourceKey,
      page_title: title,
    });
    return `/login?${params.toString()}`;
  }, [sourceKey, title]);

  // Copy button: body + an «n-label» reference list under «المراجع», so a
  // reader who copies the article keeps the [n] markers resolvable. Matches
  // PublicAnswerView.copyContent, ``referenceCopyLabel`` included — the pasted
  // list stands alone, so each wing adds what locates its document. A post
  // published before the case fields shipped has a FROZEN references_json and
  // degrades to the subject title it copies today.
  //
  // THE BODY IS STRIPPED OF ITS MARKETING CARDS FIRST. A published blog carries
  // its cover and «أبرز النقاط» panels inline as ``![…](…blog-cards…)``, which
  // render as the article but paste as raw Supabase URLs wedged between the
  // paragraphs. The clipboard gets the prose and the references — nothing else.
  const copyContent = useMemo(() => {
    const text = stripMarkdownImages(body);
    if (references.length === 0) return text;
    const refLines = [...references]
      .sort((a, b) => a.n - b.n)
      .map((ref) => `${ref.n}-${referenceCopyLabel(ref)}`)
      .join("\n");
    return text.trim().length > 0
      ? `${text}\n\nالمراجع\n${refLines}`
      : `المراجع\n${refLines}`;
  }, [body, references]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(copyContent);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can fail on insecure contexts / denied permissions; fail
      // silently — the reader can still select & copy by hand.
    }
  }, [copyContent]);

  return (
    <BlogPageShell>
      {/* max-w-6xl, not the gallery's 5xl: the desktop rail (17rem + gap-10)
          has to come out of the page's SURPLUS width. Under a 5xl container the
          reading column would pay for it and render far narrower than the same
          body does with no TOC. Every inner block stays max-w-3xl, so the wider
          container is invisible on an article without a rail. */}
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        {/* «الرئيسية / المدونة / …» — the orientation every library wing gives
            a reader who landed here from a search result, and the article's
            `BreadcrumbList` node. Inline-start aligned, above the centered hero:
            a trail is navigation chrome, not part of the title block. */}
        {breadcrumbs && (
          <div className="mx-auto mb-6 max-w-3xl">{breadcrumbs}</div>
        )}

        {/* Hero — centered kicker, title, branded byline, divider */}
        <header className="mx-auto max-w-3xl text-center">
          {subtypeLabel && (
            <span className="inline-flex items-center rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
              {subtypeLabel}
            </span>
          )}
          <h1 className="mt-4 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            {title}
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            ريحان{bylineDate ? ` · ${bylineDate}` : ""}
          </p>

          {/* Type badge + subject chips — the wing's internal-linking spine.
              Renders nothing at all on a legacy snapshot, which has neither. */}
          <SubjectChips
            type={post.type}
            subjects={post.subjects}
            className="mt-3"
          />

          {/* Unobtrusive actions under the byline: copy + chat-with-blog */}
          <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={handleCopy}
              aria-label={copied ? "تم النسخ" : "نسخ المقال"}
              className="h-7 gap-1.5 px-2 text-[11px]"
            >
              {copied ? (
                <>
                  <Check className="h-3 w-3" />
                  تم النسخ
                </>
              ) : (
                <>
                  <Copy className="h-3 w-3" />
                  نسخ المقال
                </>
              )}
            </Button>
            {/* ⚠ THIS REPLACED `ChatWithBlogButton`, WHICH RENDERED NOTHING AT
                ALL IN PRODUCTION. That component read the blog key from
                `useParams().token`, and the route segment was renamed `[token]`
                → `[slug]` when `/blog` became the three-vocabulary dispatcher —
                so `token` was `undefined` on every `/blog/*` URL and its
                `if (!token) return null` fired on every render. The article's
                only action was «نسخ المقال». Taking the key from a PROP is the
                fix: this surface is already given the address it renders.

                `ChatWithPageCta` is also the better destination. It carries the
                page through `/library-items` + `fetch_grounding` — which now
                resolves BOTH blog vocabularies — instead of the token-only
                `createBlogItem` import endpoint, which cannot serve a slug.
                `w-auto` overrides its default `w-full`; tailwind-merge keeps the
                later class. */}
            <ChatWithPageCta
              pageType="blog"
              pageId={sourceKey}
              pageTitle={title}
              loginHref={loginHref}
              className="h-7 w-auto gap-1.5 px-2 text-[11px]"
            />
          </div>
        </header>

        <div className="mx-auto mt-6 max-w-3xl border-b" />

        {/* Body (+ the TOC pair when there are enough headings). The page is
            dir="rtl", so grid column 1 (the article) starts on the RIGHT and the
            rail — column 2 — lands on the LEFT, sticky beside the scrolling
            body. Same shape /compliance/{slug} and /regulations/{slug} use.
            Without a TOC there is no grid at all. */}
        <div
          className={
            showToc
              ? "mt-8 lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start lg:gap-10"
              : "mt-8 lg:mx-auto lg:max-w-3xl"
          }
        >
          <div className="min-w-0 lg:max-w-3xl">
            {showToc && (
              <div className="mb-6 lg:hidden">
                {/* Collapsed on mobile for the reason the library wings
                    learned: an expanded index puts the whole index between the
                    reader and the lede. */}
                <TocList
                  entries={tocEntries}
                  title="محتويات المدونة"
                  badge={badge}
                  defaultOpen={false}
                />
                {/* …and the floating pill takes over once that list has
                    scrolled away. Its in-flow sentinel is rendered by the
                    widget itself, so it MUST stay directly after the list. */}
                <TocFloating
                  entries={tocEntries}
                  title="محتويات المدونة"
                  badge={badge}
                  spyPrefix={TOC_SPY_PREFIX}
                />
              </div>
            )}

            {/* `text-read` (18/17px) is the long-form reading scale, registered
                in `lib/utils.ts`'s tailwind-merge classGroups so `cn()` cannot
                strip it. `headingAnchors` additionally puts `MarkdownRenderer`
                in its own reading mode (paragraphs and list items on the same
                scale, editorial heading ladder) and emits the `slugifyHeading`
                ids the TOC links to. */}
            <article className="text-read">
              <MarkdownRenderer
                content={body}
                onCitationClick={handleBodyCitationClick}
                headingAnchors
              />
            </article>

            {/* References — beneath the article, inside the reading column so
                the citation cards keep the body's measure. */}
            {references.length > 0 && (
              <div className="mt-8">
                <ReferencePanel
                  blogToken={sourceKey}
                  references={references}
                  focusedReferenceN={focusedN}
                  onFlashDone={handleFlashDone}
                />
              </div>
            )}

            {/* ⚠ UNCONDITIONAL, and that is the point — the notice is required
                on every blog, so it cannot sit inside the `references.length`
                branch above the way the artifact's does. A post that cites
                nothing is if anything the one that needs it most.

                Same component the search artifact uses, imported rather than
                re-typed: `AGENT_OUTPUT_DISCLAIMER_AR` is the single source of
                this wording (it used to be baked into `content_md` and was
                pulled out to one place in migration 074), and a second copy is
                a second copy to keep in sync with legal. */}
            <AgentOutputDisclaimer />
          </div>

          {showToc && (
            <aside className="hidden lg:sticky lg:top-24 lg:block">
              <TocRail
                entries={tocEntries}
                title="محتويات المدونة"
                badge={badge}
                spyPrefix={TOC_SPY_PREFIX}
              />
            </aside>
          )}
        </div>

        {/* «اقرأ تاليًا» — the last in-flow content, above the conversion CTA
            the shell adds. Full container width, OUTSIDE the reading column and
            its TOC grid, exactly as the library wings place it: cards to scan,
            not text to read. Renders nothing when nothing shares a topic —
            `RelatedStrip` collapses on empty children, and the route passes the
            slot only when the list came back non-empty. */}
        {relatedStrip && <div className="mt-12">{relatedStrip}</div>}
      </main>

      {/* The «اسأل ريحان» FAB — bottom-LEFT, the physical corner opposite the
          TOC pill's `start-4`, both `z-40`, neither ever overlapping the other.
          Every other reading wing has mounted this for months; the مدونة was the
          only one without it, and it is the wing anonymous readers arrive on
          from Google.

          ⚠ `pageId` MUST be `sourceKey`, not the title or a rebuilt path: it is
          the grounding key, and `ask_service._ground_blog` resolves a 32-hex
          token against `blog_posts` and anything else against `public_blogs`.
          Handing it the wrong string does not error — it grounds on an empty
          document and the answer quietly stops being about this article. */}
      <AskRayhanWidget pageType="blog" pageId={sourceKey} pageTitle={title} />
    </BlogPageShell>
  );
}
