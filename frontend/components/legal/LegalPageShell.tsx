import Link from "next/link";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { LegalLinksFooter } from "@/components/legal/LegalLinksFooter";

interface Props {
  title: string;
  content: string;
}

/**
 * RTL shell for the public legal pages (/terms, /privacy). Server component —
 * renders the client MarkdownRenderer with a baked markdown string. The
 * `onCitationClick` prop is intentionally omitted so any `[n]` tokens in the
 * legal text stay plain text rather than becoming citation buttons.
 */
export function LegalPageShell({ title, content }: Props) {
  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-3xl px-4 py-10">
        {/* Header — ريحان logo box + page title */}
        <header className="mb-8 flex flex-col items-center gap-4 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-xl font-bold text-primary-foreground">
            ريحان
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            {title}
          </h1>
        </header>

        {/* Document body */}
        <article className="markdown-content">
          <MarkdownRenderer content={content} />
        </article>

        {/* Back link + footer links */}
        <footer className="mt-10 flex flex-col items-center gap-4 border-t border-border pt-6">
          <Link
            href="/chat"
            className="text-sm text-primary underline-offset-4 transition-colors hover:underline"
          >
            العودة إلى ريحان
          </Link>
          <LegalLinksFooter />
        </footer>
      </main>
    </div>
  );
}
