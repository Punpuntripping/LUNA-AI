import { TemplatesLayoutClient } from "@/components/templates/TemplatesLayoutClient";

// Next.js App Router requires default export for layout files
// eslint-disable-next-line import/no-default-export
export default function TemplatesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <TemplatesLayoutClient>{children}</TemplatesLayoutClient>;
}
