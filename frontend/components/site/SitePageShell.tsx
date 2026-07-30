import { SiteHeader } from "@/components/site/SiteHeader";
import { SiteFooter } from "@/components/site/SiteFooter";

interface SitePageShellProps {
  children: React.ReactNode;
  /** Render the shared site footer. Default true; pass false for a page that
   *  supplies its own closing chrome. */
  footer?: boolean;
}

/**
 * The single wrapper every public, non-sidebar page uses: global header +
 * page content + site footer, as a full-height flex column. `<html>` already
 * carries `dir="rtl"` (root layout) so the shell doesn't repeat it.
 *
 * Callers supply their own `<main>` / max-width inside `children` — the shell
 * only owns the chrome, so a wide directory grid and a narrow article column
 * both sit correctly between the same header and footer.
 */
export function SitePageShell({ children, footer = true }: SitePageShellProps) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <SiteHeader />
      <div className="flex-1">{children}</div>
      {footer && <SiteFooter />}
    </div>
  );
}
