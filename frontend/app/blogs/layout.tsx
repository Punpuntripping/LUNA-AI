import { BlogsLayoutClient } from "@/components/blogs/BlogsLayoutClient";

// Next.js App Router requires default export for layout files
// eslint-disable-next-line import/no-default-export
export default function BlogsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <BlogsLayoutClient>{children}</BlogsLayoutClient>;
}
