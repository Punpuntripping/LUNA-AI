import { SidebarPageShell } from "@/components/shell/SidebarPageShell";

/**
 * «مكتبتي» gets the app shell (Sidebar, no public site header) so it reads as
 * one of the three per-user collections (قوالبي · مدوناتي · مكتبتي), all
 * reached from the sidebar nav. Scoped to /library/mine only — the public
 * /library/{sector} pages keep their SitePageShell chrome.
 *
 * The shelf content relied on SitePageShell for page scroll; inside the
 * h-screen shell it needs its own scroll container.
 */
// Next.js App Router requires a default export for layout files
// eslint-disable-next-line import/no-default-export
export default function MyLibraryLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SidebarPageShell>
      <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
    </SidebarPageShell>
  );
}
