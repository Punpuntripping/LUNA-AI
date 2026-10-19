import { Directory, File, Paths } from "expo-file-system";
import { Platform, Share } from "react-native";

import type { ExportFormat } from "@contracts";

import { exportDocument } from "@/api/workspace";

import { parseLastFolder, pickerStartUri, serializeLastFolder } from "@/lib/saf";

import { EXPORT_MIME, exportDocTitle, exportFileName } from "./action-bar";

export type ExportOutcome = "shared" | "saved" | "cancelled";

/**
 * «PDF» / «Word» — the web's ExportMenu downloads the file; a phone hands it
 * on instead, with expo-file-system only (no new native module):
 *
 * - iOS: written to the cache, then the native share sheet («حفظ في الملفات»,
 *   Mail, WhatsApp…) — `Share.share({ url })` takes a file URL on iOS.
 * - Android: RN's Share carries text only, so the system folder picker (SAF)
 *   asks where to save it, and the file is written there. The picker opens in
 *   the folder used last time, else «Documents» — never «Download», whose root
 *   Android 11+ refuses («Can't use this folder»), which forced the user to
 *   make a sub-folder first. So «Use this folder» is one tap. (A true
 *   «save to Downloads» needs a native module — see the QA report F2.)
 *
 * `beforePresent` runs after the render, before any system UI is shown (the
 * caller waits there for its own menu sheet to finish dismissing — iOS will
 * not present over a modal that is still animating out).
 *
 * Throws the `ApiError` of a failed render (Arabic message) — the caller shows it.
 */
export async function exportAndDeliver(
  format: ExportFormat,
  title: string,
  markdown: string,
  beforePresent: () => Promise<void>,
): Promise<ExportOutcome> {
  const bytes = await exportDocument({ format, title: exportDocTitle(title), markdown });
  const name = exportFileName(title, format);
  await beforePresent();

  if (Platform.OS === "ios") {
    const file = new File(Paths.cache, name);
    file.create({ overwrite: true });
    file.write(bytes);
    const res = await Share.share({ url: file.uri, title: name });
    return res.action === Share.dismissedAction ? "cancelled" : "shared";
  }

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
