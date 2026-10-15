import { WEB_URL } from "@/config/env";

// Pure (no native imports) so it is unit-tested under node.

/**
 * `library_url` (a site path like `/regulations/{slug}`) → the public page on
 * rayhanai.com. Anything that is not a same-site path (`//host`, schemes,
 * blanks) → null, so the button is dropped — never guessed.
 */
export function libraryWebUrl(libraryUrl: string | null | undefined): string | null {
  const path = (libraryUrl ?? "").trim();
  if (!path) return null;
  if (path.startsWith("/") && !path.startsWith("//")) return `${WEB_URL}${path}`;
  if (path.toLowerCase().startsWith(`${WEB_URL}/`)) return path;
  return null;
}
