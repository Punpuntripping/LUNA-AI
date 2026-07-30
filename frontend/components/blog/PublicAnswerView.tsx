"use client";

import { useCallback, useState } from "react";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { ReferencePanel, referenceLabel } from "@/components/workspace/ReferencePanel";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { ChatWithBlogButton } from "@/components/blog/ChatWithBlogButton";
import type { BlogPostPublic } from "@/types";

// Subtype → Arabic chip label. Mirrors WorkspaceCard.tsx SUBTYPE_LABEL so the
// public page speaks the same vocabulary as the in-app workspace.
const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

interface PublicAnswerViewProps {
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
 * Public, read-only reading surface for a shared artifact in question mode
 * (السؤال block → answer → references).
 *
 * Anon-accessible: receives the frozen snapshot (``content_md`` +
 * ``references``) as props — NO useWorkspaceItemReferences. The only
 * auth-aware element is the ``ChatWithBlogButton`` action, which degrades to
 * a login-redirect for anonymous readers.
 *
 * The brand header, «جرّب ريحان مجاناً» CTA, and footer come from the shared
 * ``BlogPageShell`` — this component only supplies the reading ``<main>``.
 *
 * Citation fluidity mirrors AgentSearchViewer EXACTLY: clicking ``[n]`` in the
 * body sets ``focusedN`` (re-armed via requestAnimationFrame so repeat clicks
 * on the same N re-fire), which drives ReferencePanel's ``focusedReferenceN``
 * (scroll-to-card / open source popup). ``handleFlashDone`` clears it.
 */
export function PublicAnswerView({ post, blogToken }: PublicAnswerViewProps) {
  const [focusedN, setFocusedN] = useState<number | null>(null);

  const handleBodyCitationClick = useCallback((n: number) => {
    // Clear first so ReferencePanel's effect fires even on consecutive clicks
    // of the same N (the effect only runs when the value changes).
    setFocusedN(null);
    window.requestAnimationFrame(() => setFocusedN(n));
  }, []);

  const handleFlashDone = useCallback(() => {
    setFocusedN(null);
  }, []);

  const references = post.references ?? [];
  const subtypeLabel = post.subtype
    ? SUBTYPE_LABEL[post.subtype] ?? post.subtype
    : null;

  // A تحليل قانوني shared via the السؤال template can carry NO derived question.
  // Render the «السؤال» card only when there's a real question — otherwise it's
  // a hollow box. When absent, the title becomes a centered hero (article look).
  const hasQuestion = (post.question_text ?? "").trim().length > 0;
  const title = (post.title ?? "").trim();
  const heading = title || (post.question_text ?? "").trim();
  // Show the heading whenever there's something to show, but never duplicate
  // the السؤال card (no distinct title + a question → the card already shows it).
  const showHeading = heading.length > 0 && !(hasQuestion && !title);
  const heroHeading = !hasQuestion;

  // Copy button: body + a plain «n-title» reference list under «المراجع», so a
  // reader who copies the answer keeps the [n] markers resolvable. Matches
  // AgentSearchViewer.copyContent.
  const body = post.content_md ?? "";
  const copyContent =
    references.length === 0
      ? body
      : (() => {
          const refLines = [...references]
            .sort((a, b) => a.n - b.n)
            .map((ref) => `${ref.n}-${referenceLabel(ref)}`)
            .join("\n");
          return body.trim().length > 0
            ? `${body}\n\nالمراجع\n${refLines}`
            : `المراجع\n${refLines}`;
        })();

  return (
    <BlogPageShell>
      {/* Reading column */}
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6">
        {/* السؤال block — only when there's a real question (no hollow box) */}
        {hasQuestion && (
          <section className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-semibold text-muted-foreground">
                السؤال
              </span>
              {subtypeLabel && (
                <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                  {subtypeLabel}
                </span>
              )}
            </div>
            <p className="whitespace-pre-wrap text-base font-medium leading-relaxed text-foreground">
              {post.question_text}
            </p>
          </section>
        )}

        {/* Heading — centered. When there's no question, it becomes a centered
            hero with a subtype kicker (BlogArticleView look) so a question-less
            share reads as a clean article rather than an empty box. */}
        {showHeading && (
          <header
            className={`mx-auto max-w-3xl text-center${hasQuestion ? " mt-6" : ""}`}
          >
            {heroHeading && subtypeLabel && (
              <span className="inline-flex items-center rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                {subtypeLabel}
              </span>
            )}
            <h1
              className={`text-2xl font-bold tracking-tight text-foreground sm:text-3xl${
                heroHeading && subtypeLabel ? " mt-4" : ""
              }`}
            >
              {heading}
            </h1>
          </header>
        )}

        {/* Chat-with-blog action — between the question/heading and the answer */}
        <div className="mt-4 flex justify-center">
          <ChatWithBlogButton className="h-8 gap-1.5 px-3 text-xs" />
        </div>

        {/* Answer + references — same fluidity as the in-app artifact view */}
        <section className="mt-4 flex min-h-[40vh] flex-col rounded-xl border bg-card shadow-sm">
          <ArtifactPreview
            content={body}
            copyContent={copyContent}
            onCitationClick={handleBodyCitationClick}
            footer={
              references.length > 0 ? (
                <ReferencePanel
              blogToken={blogToken}
                  references={references}
                  focusedReferenceN={focusedN}
                  onFlashDone={handleFlashDone}
                />
              ) : null
            }
          />
        </section>
      </main>
    </BlogPageShell>
  );
}
