import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { LegalLinksFooter } from "@/components/legal/LegalLinksFooter";
import { SitePageShell } from "@/components/site/SitePageShell";

interface Props {
  title: string;
  content: string;
}

/**
 * RTL shell for the public legal pages (/terms, /privacy, /masking,
 * /promo-terms). Wraps the
 * baked markdown body in the global `SitePageShell`, so the brand + nav come
 * from the shared header rather than a one-off centered logo box.
 *
 * The `onCitationClick` prop is intentionally omitted so any `[n]` tokens in the
 * legal text stay plain text rather than becoming citation buttons.
 */
export function LegalPageShell({ title, content }: Props) {
  return (
    <SitePageShell>
      <main className="mx-auto max-w-3xl px-4 py-10">
        <header className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            {title}
          </h1>
        </header>

        <article className="markdown-content">
          <MarkdownRenderer content={content} />
        </article>

        <footer className="mt-10 flex flex-col items-center gap-4 border-t border-border pt-6">
          <LegalLinksFooter />
        </footer>
      </main>
    </SitePageShell>
  );
}
