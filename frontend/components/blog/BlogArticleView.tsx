"use client";

import { useCallback, useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { BlogTableOfContents } from "@/components/blog/BlogTableOfContents";
import { ChatWithBlogButton } from "@/components/blog/ChatWithBlogButton";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import {
  ReferencePanel,
  referenceLabel,
} from "@/components/workspace/ReferencePanel";
import { Button } from "@/components/ui/button";
import { extractHeadings } from "@/lib/markdown/headings";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import type { BlogPostPublic } from "@/types";

// Subtype → Arabic kicker label. Mirrors PublicAnswerView / WorkspaceCard so
// the editorial page speaks the same vocabulary as the in-app workspace.
const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

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

interface BlogArticleViewProps {
  post: BlogPostPublic;
  /**
   * The post's public token — the address for the METERED source reveal
   * (`GET /public/blog/{token}/references/{n}/source`).
   *
   * A reader is not the author, so the workspace reveal endpoint would
   * 404 them; the unguessable token is the capability here. Passing it
   * is what keeps «عرض المصدر» and the [n] preview working on a public
   * post — anonymous readers included, who get the «سجّل مجاناً» card.
   */
  blogToken: string;
}

/**
 * Title-mode (مدونة) public reading surface: an editorial blog article with a
 * centered hero title, branded byline, a «محتويات» table-of-contents rail
 * (when the body has ≥2 headings), the article body, and the reference panel.
 *
 * Anon-accessible: receives the frozen snapshot (``content_md`` +
 * ``references``) as props. The only auth-aware element is the
 * ``ChatWithBlogButton`` action, which degrades to a login-redirect for
 * anonymous readers.
 *
 * Citation fluidity mirrors PublicAnswerView / AgentSearchViewer EXACTLY:
 * clicking ``[n]`` in the body sets ``focusedN`` (re-armed via
 * requestAnimationFrame so repeat clicks on the same N re-fire), which drives
 * ReferencePanel's ``focusedReferenceN``. ``handleFlashDone`` clears it.
 *
 * The brand header, «جرّب ريحان مجاناً» CTA, and footer come from
 * ``BlogPageShell`` — they are NOT duplicated here.
 */
export function BlogArticleView({ post, blogToken }: BlogArticleViewProps) {
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

  const headings = useMemo(() => extractHeadings(body), [body]);
  const showToc = headings.length >= 2;

  // Copy button: body + a plain «n-title» reference list under «المراجع», so a
  // reader who copies the article keeps the [n] markers resolvable. Matches
  // PublicAnswerView.copyContent.
  const copyContent = useMemo(() => {
    if (references.length === 0) return body;
    const refLines = [...references]
      .sort((a, b) => a.n - b.n)
      .map((ref) => `${ref.n}-${referenceLabel(ref)}`)
      .join("\n");
    return body.trim().length > 0
      ? `${body}\n\nالمراجع\n${refLines}`
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
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
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

          {/* Unobtrusive actions under the byline: copy + chat-with-blog */}
          <div className="mt-4 flex justify-center gap-2">
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
            <ChatWithBlogButton className="h-7 gap-1.5 px-2 text-[11px]" />
          </div>
        </header>

        <div className="mx-auto mt-6 max-w-3xl border-b" />

        {/* Body (+ TOC rail when there are enough headings) */}
        {showToc ? (
          <div className="mt-8 lg:grid lg:grid-cols-[240px_1fr] lg:gap-8">
            {/* TOC FIRST in DOM → in RTL it lands on the inline-start (right).
                On mobile it reads as a bordered card above the article; on lg+
                it becomes a borderless sticky rail. One instance, one observer. */}
            <BlogTableOfContents
              headings={headings}
              className="mb-6 rounded-xl border bg-card/50 p-4 lg:mb-0 lg:self-start lg:rounded-none lg:border-0 lg:bg-transparent lg:p-0 lg:sticky lg:top-20"
            />
            <article className="min-w-0">
              <MarkdownRenderer
                content={body}
                onCitationClick={handleBodyCitationClick}
                headingAnchors
              />
            </article>
          </div>
        ) : (
          <article className="mx-auto mt-8 max-w-3xl">
            <MarkdownRenderer
              content={body}
              onCitationClick={handleBodyCitationClick}
              headingAnchors
            />
          </article>
        )}

        {/* References — full width beneath the article */}
        {references.length > 0 && (
          <div className="mx-auto mt-8 max-w-3xl">
            <ReferencePanel
              blogToken={blogToken}
              references={references}
              focusedReferenceN={focusedN}
              onFlashDone={handleFlashDone}
            />
          </div>
        )}
      </main>
    </BlogPageShell>
  );
}
