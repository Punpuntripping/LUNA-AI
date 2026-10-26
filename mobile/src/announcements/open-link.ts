import * as WebBrowser from "expo-web-browser";

import { announcementLinkUrl } from "./logic";

/**
 * A message's link button, opened the way the app opens every site page (the
 * in-app browser, plan K3): an in-site path → rayhanai.com + path, an
 * `https://` URL as is. Resolves once the browser is closed (iOS) or opened
 * (Android); never rejects.
 */
export async function openAnnouncementLink(linkUrl: string | null): Promise<void> {
  const url = announcementLinkUrl(linkUrl);
  if (!url) return;
  try {
    await WebBrowser.openBrowserAsync(url);
  } catch {
    // Already open / no browser — nothing to tell the user.
  }
}
