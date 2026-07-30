import { BlogConversionCta } from "@/components/blog/BlogConversionCta";
import { SitePageShell } from "@/components/site/SitePageShell";

interface BlogPageShellProps {
  children: React.ReactNode;
  /** Show the «جرّب ريحان مجاناً» conversion block above the footer. Default true. */
  showCta?: boolean;
}

/**
 * Shared public-page chrome for every مدونة surface — the question page, the
 * editorial article page, and the gated directory. Delegates the header +
 * footer to the global `SitePageShell` (so blog pages carry the exact same
 * nav as the rest of the site) and only adds the blog-specific conversion CTA
 * between the content and the footer.
 *
 * The CTA is rendered as the last content block (inside the shell's content
 * area, above the footer) within its own centered `max-w-3xl` wrapper so it
 * reads correctly regardless of how wide the children content column is — the
 * directory grid is wider than the article column.
 *
 * Auth-aware chrome comes for free from `SitePageShell`: `HeaderAuthActions`
 * swaps the login/signup buttons for «العودة إلى ريحان» when a session exists,
 * and `BlogConversionCta` hides the signup pitch from signed-in readers.
 */
export function BlogPageShell({ children, showCta = true }: BlogPageShellProps) {
  return (
    <SitePageShell>
      {children}
      {showCta && <BlogConversionCta />}
    </SitePageShell>
  );
}
