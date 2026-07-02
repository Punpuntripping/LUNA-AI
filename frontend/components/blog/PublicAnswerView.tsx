"use client";

import { useCallback, useState } from "react";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { ReferencePanel, referenceLabel } from "@/components/workspace/ReferencePanel";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
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
}

/**
 * Public, read-only reading surface for a shared artifact in question mode
 * (السؤال block → answer → references).
 *
 * Anon-accessible: receives the frozen snapshot (``content_md`` +
 * ``references``) as props — NO auth hooks, NO useWorkspaceItemReferences.
 *
 * The brand header, «جرّب ريحان مجاناً» CTA, and footer come from the shared
 * ``BlogPageShell`` — this component only supplies the reading ``<main>``.
 *
 * Citation fluidity mirrors AgentSearchViewer EXACTLY: clicking ``[n]`` in the
 * body sets ``focusedN`` (re-armed via requestAnimationFrame so repeat clicks
 * on the same N re-fire), which drives ReferencePanel's ``focusedReferenceN``
 * (scroll-to-card / open source popup). ``handleFlashDone`` clears it.
 */
export function PublicAnswerView({ post }: PublicAnswerViewProps) {
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

        {/* Answer + references — same fluidity as the in-app artifact view */}
        <section className="mt-4 flex min-h-[40vh] flex-col rounded-xl border bg-card shadow-sm">
          <ArtifactPreview
            content={body}
            copyContent={copyContent}
            onCitationClick={handleBodyCitationClick}
            footer={
              references.length > 0 ? (
                <ReferencePanel
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
