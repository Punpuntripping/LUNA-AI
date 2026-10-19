import { Directory, File, Paths } from "expo-file-system";
import { Platform, Share } from "react-native";

import type { ExportFormat } from "@contracts";

import { exportDocument } from "@/api/workspace";

import { EXPORT_MIME, exportDocTitle, exportFileName } from "./action-bar";

export type ExportOutcome = "shared" | "saved" | "cancelled";

/**
 * «PDF» / «Word» — the web's ExportMenu downloads the file; a phone hands it
 * on instead, with expo-file-system only (no new native module):
 *
 * - iOS: written to the cache, then the native share sheet («حفظ في الملفات»,
 *   Mail, WhatsApp…) — `Share.share({ url })` takes a file URL on iOS.
 * - Android: RN's Share carries text only, so the system folder picker (SAF)
 *   asks where to save it, and the file is written there.
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
    dir = await Directory.pickDirectoryAsync();
  } catch {
    return "cancelled"; // picker dismissed
  }
  const file = dir.createFile(name, EXPORT_MIME[format]);
  file.write(bytes);
  return "saved";
}
