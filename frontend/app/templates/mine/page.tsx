"use client";

import { MyTemplatesGrid } from "@/components/templates/MyTemplatesGrid";

/**
 * `/templates/mine` — «قوالبي» at the same `/mine` address `/library/mine` and
 * `/blogs/mine` use, so the three per-user collections share one URL
 * convention. Reached from the sidebar's «عرض كل القوالب» button.
 *
 * Unlike the bare `/templates` landing (a "pick one from the list" prompt that
 * sits beside the editor), this is the browsable collection: every قالب as a
 * card, opening `/templates/{id}`.
 *
 * A static `mine` segment beats the sibling `[id]` dynamic segment in the App
 * Router, and template ids are UUIDs — no قالب can shadow it.
 */

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function MyTemplatesMinePage() {
  return <MyTemplatesGrid />;
}
