import * as WebBrowser from "expo-web-browser";

const CITE_RE = /^cite:(\d+)$/;

/** `cite:N` → N; anything else → null. */
export function citationNumber(href: string): number | null {
  const m = CITE_RE.exec(href.trim());
  if (!m) return null;
  const n = Number.parseInt(m[1], 10);
  return n > 0 ? n : null;
}

/** Only http(s) links open (in-app browser); every other scheme is dropped (plan K3). */
export function isExternalHref(href: string): boolean {
  return /^https?:\/\//i.test(href.trim());
}

export function openExternal(href: string): void {
  if (!isExternalHref(href)) return;
  WebBrowser.openBrowserAsync(href.trim()).catch(() => undefined);
}

/** Bidi isolate: a `[4]` marker keeps its brackets on its number inside Arabic
 *  text (web CitationMarker: dir="ltr" + unicode-bidi:isolate). */
export const LRI = "⁦";
export const PDI = "⁩";
