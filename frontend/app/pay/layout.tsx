import Link from "next/link";

/**
 * Chrome for the checkout flow — deliberately thinner than `SitePageShell`.
 *
 * No nav, no dropdowns, no footer link farm: every extra exit on a payment
 * screen is a way to lose a purchase halfway through, and the one exit that
 * matters (back to the plans) is right here. `/pay` sits OUTSIDE AuthGuard's
 * `PUBLIC_PREFIXES`, so both routes beneath this layout are authed for free —
 * an anonymous visitor is redirected to /login and returned by `?next=`.
 */
// Next.js App Router requires a default export for layout files.
// eslint-disable-next-line import/no-default-export
export default function PayLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <Link
            href="/chat"
            className="text-base font-bold text-foreground"
            data-testid="pay-home-link"
          >
            ريحان
          </Link>
          <Link
            href="/pricing"
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            data-testid="pay-back-to-pricing"
          >
            العودة إلى الباقات
          </Link>
        </div>
      </header>
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
        {children}
      </main>
    </div>
  );
}
