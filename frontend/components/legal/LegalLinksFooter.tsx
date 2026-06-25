import Link from "next/link";
import { LEGAL_ROUTES } from "@/lib/legal";

/**
 * Tiny muted footer linking to the public legal pages. Rendered under the login
 * form and at the bottom of every legal page (LegalPageShell). Server-safe — no
 * client interactivity beyond Next's <Link>.
 */
export function LegalLinksFooter() {
  return (
    <nav className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
      <Link
        href={LEGAL_ROUTES.terms}
        className="transition-colors hover:text-foreground"
      >
        الشروط والأحكام
      </Link>
      <span aria-hidden="true">·</span>
      <Link
        href={LEGAL_ROUTES.privacy}
        className="transition-colors hover:text-foreground"
      >
        سياسة الخصوصية
      </Link>
      <span aria-hidden="true">·</span>
      <Link href="/pricing" className="transition-colors hover:text-foreground">
        الأسعار
      </Link>
    </nav>
  );
}
