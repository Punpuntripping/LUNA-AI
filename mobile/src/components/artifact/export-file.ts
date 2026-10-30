import { Directory, File, Paths } from "expo-file-system";
import { Platform, Share } from "react-native";

import type { ExportFormat } from "@contracts";

import { exportDocument } from "@/api/workspace";

import { parseLastFolder, pickerStartUri, serializeLastFolder } from "@/lib/saf";

import { EXPORT_MIME, exportDocTitle, exportFileName, isPreviewLockedBody } from "./action-bar";

export type ExportOutcome = "shared" | "saved" | "cancelled";

/**
 * How the rendered file reaches the user.
 * - `share` (default): the system share sheet — on Android it offers Drive,
 *   Gmail, WhatsApp, «حفظ في Drive»…; on iOS also «حفظ في الملفات».
 * - `folder` (Android only): the system folder picker (SAF) and the file is
 *   written there — kept for a future «حفظ على الجهاز» entry; on iOS it is
 *   the share sheet too.
 */
export type ExportDelivery = "share" | "folder";

/**
 * «PDF» / «Word» — the web's ExportMenu downloads the file; a phone hands it on.
 * The file is written to the app cache and handed to the native share sheet:
 * iOS via RN `Share.share({ url })`, Android via expo-sharing (RN's Share
 * carries text only there).
 *
 * The folder path opens the picker in the folder used last time, else
 * «Documents» — never «Download», whose root Android 11+ refuses («Can't use
 * this folder»). It is also the fallback when the installed build predates
 * expo-sharing (the native module is missing → the import throws).
 *
 * `beforePresent` runs after the render, before any system UI is shown (the
 * caller waits there for its own menu sheet to finish dismissing — iOS will
 * not present over a modal that is still animating out).
 *
 * Throws the `ApiError` of a failed render (Arabic message) — the caller shows it.
 *
 * A preview-locked item is never exported (wi_preview_lock.md §9.4): the bar
 * offers no PDF there, and a raw locked body that still reaches this function
 * is refused before any request (the caller's Arabic fallback is shown).
 */
export async function exportAndDeliver(
  format: ExportFormat,
  title: string,
  markdown: string,
  beforePresent: () => Promise<void>,
  delivery: ExportDelivery = "share",
): Promise<ExportOutcome> {
  if (isPreviewLockedBody(markdown)) throw new Error("wi_preview_locked");
  const bytes = await exportDocument({ format, title: exportDocTitle(title), markdown });
  const name = exportFileName(title, format);
  await beforePresent();

  if (Platform.OS === "ios") {
    const file = writeToCache(name, bytes);
    const res = await Share.share({ url: file.uri, title: name });
    return res.action === Share.dismissedAction ? "cancelled" : "shared";
  }

  if (delivery === "share") {
    const sharing = await loadSharing();
    if (sharing) {
      const file = writeToCache(name, bytes);
      // Android's chooser does not report whether a target was picked.
      await sharing.shareAsync(file.uri, { mimeType: EXPORT_MIME[format], dialogTitle: name });
      return "shared";
    }
  }
  return saveToFolder(name, format, bytes);
}

function writeToCache(name: string, bytes: Uint8Array): File {
  const file = new File(Paths.cache, name);
  file.create({ overwrite: true });
  file.write(bytes);
  return file;
}

type SharingModule = typeof import("expo-sharing");

// Lazy: expo-sharing resolves its native module at import time and throws when
// the installed dev build has none — a static import would break the whole
// action bar on such a build instead of falling back to the folder picker.
async function loadSharing(): Promise<SharingModule | null> {
  try {
    return await import("expo-sharing");
  } catch {
    return null;
  }
}

async function saveToFolder(name: string, format: ExportFormat, bytes: Uint8Array): Promise<ExportOutcome> {
  let dir: Directory;
  try {
    dir = await Directory.pickDirectoryAsync(pickerStartUri(readLastFolder()));
  } catch {
    return "cancelled"; // picker dismissed
  }
  const file = dir.createFile(name, EXPORT_MIME[format]);
  file.write(bytes);
  rememberFolder(dir.uri);
  return "saved";
}

// The picker keeps a persisted grant on the chosen folder; the app only
// remembers its URI so the next export opens there.
function lastFolderFile(): File {
  return new File(Paths.document, "export-folder.json");
}

function readLastFolder(): string | null {
  try {
    const file = lastFolderFile();
    return file.exists ? parseLastFolder(file.textSync()) : null;
  } catch {
    return null;
  }
}

function rememberFolder(treeUri: string): void {
  try {
    const file = lastFolderFile();
    if (!file.exists) file.create();
    file.write(serializeLastFolder(treeUri));
  } catch {
    // Best effort: the next export just opens in «Documents».
  }
}
