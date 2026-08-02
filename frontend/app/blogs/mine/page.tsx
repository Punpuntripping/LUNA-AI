"use client";

import { MyBlogsGrid } from "@/components/blogs/MyBlogsGrid";

/**
 * `/blogs/mine` — «مدوناتي» at the same `/mine` address `/library/mine` uses,
 * so the three per-user collections (قوالبي · مدوناتي · مكتبتي) share one URL
 * convention. Reached from the sidebar's «عرض كل المدونات» button.
 *
 * A static `mine` segment beats the sibling `[token]` dynamic segment in the
 * App Router, and share tokens are random slugs — no post can shadow it.
 *
 * The route sits under `app/blogs/layout.tsx`, so the sidebar shell comes for
 * free.
 */

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function MyBlogsMinePage() {
  return <MyBlogsGrid />;
}
