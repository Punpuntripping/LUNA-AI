"use client";

import { MyBlogsGrid } from "@/components/blogs/MyBlogsGrid";

// مدوناتي landing (/blogs). The route-group layout supplies the sidebar; the
// grid itself lives in MyBlogsGrid so this page and its explicit twin
// `/blogs/mine` render the exact same surface from one implementation.

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function MyBlogsPage() {
  return <MyBlogsGrid />;
}
