import type { Metadata } from "next";
import { SitePageShell } from "@/components/site/SitePageShell";
import { MyLibraryPage } from "@/components/library/mine/MyLibraryPage";

export const metadata: Metadata = {
  title: "مكتبتي — ريحان",
  description: "المصادر التي فتحتها أو حفظتها في مكتبة ريحان القانونية.",
  // Per-user surface: nothing here belongs in an index.
  robots: { index: false, follow: false },
};

/**
 * `/library/mine` — «مكتبتي», the authed per-user shelf (PART 5B).
 *
 * `force-dynamic` is a HARD requirement, not a preference: this route is authed
 * and per-user, so it must never be statically rendered or ISR-cached (D11 —
 * per-user bytes reaching a shared cache is the one correctness property the
 * whole gating design rests on). The shelf itself is fetched client-side with
 * the bearer token; nothing user-specific is produced by this server render.
 */
export const dynamic = "force-dynamic";
export const revalidate = 0;

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function Page() {
  return (
    <SitePageShell>
      <MyLibraryPage />
    </SitePageShell>
  );
}
