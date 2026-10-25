import type { ImagePickerAsset } from "expo-image-picker";

import { imageExtension, isGeneratedFileName, MAX_ATTACHMENTS, MSG_CAMERA_DENIED, photoFileName, type PickedFile } from "./limits";

// F1 — the three sources of the composer's attach sheet. Each returns the
// picked files (empty = the user backed out) or throws PickerDenied with the
// Arabic text to show. Copies land in the app cache (file:// URIs that
// expo-file-system can read in byte ranges).
//
// The picker modules are imported lazily: a dev build without their native
// code must not crash at app start (only the pick itself fails, in Arabic).
// Photos use the system photo picker only — no media-library permission
// (READ_EXTERNAL_STORAGE is blocked in app.json); the camera asks for itself.

export class PickerDenied extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PickerDenied";
  }
}

/** «ملف» — PDFs and images from Files / Drive / downloads. */
export async function pickDocuments(): Promise<PickedFile[]> {
  const DocumentPicker = await import("expo-document-picker");
  const res = await DocumentPicker.getDocumentAsync({
    type: ["application/pdf", "image/*"],
    multiple: true,
    copyToCacheDirectory: true,
  });
  if (res.canceled) return [];
  return res.assets.map((a) => ({
    uri: a.uri,
    name: a.name,
    size: typeof a.size === "number" ? a.size : null,
    mimeType: a.mimeType ?? null,
  }));
}

function fromImageAsset(a: ImagePickerAsset, name: string): PickedFile {
  return {
    uri: a.uri,
    name,
    size: typeof a.fileSize === "number" ? a.fileSize : null,
    mimeType: a.mimeType ?? null,
  };
}

/** «صورة 2026-10-25 14-03.jpeg» for an asset (QA F9 — never its cache UUID). */
function readableName(a: ImagePickerAsset, at: Date, index: number): string {
  return photoFileName(at, imageExtension(a.fileName || a.uri, a.mimeType), index);
}

/** «الصور» — the system photo picker (no permission prompt). iOS hands HEIC
 *  over as JPEG where it can (Compatible); anything else is converted while
 *  preparing. Images only. A photo keeps its real name; one that only has the
 *  picker's UUID gets a dated one (numbered when several are picked). */
export async function pickPhotos(remaining: number): Promise<PickedFile[]> {
  const ImagePicker = await import("expo-image-picker");
  const res = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ["images"],
    allowsMultipleSelection: true,
    selectionLimit: Math.max(1, Math.min(remaining, MAX_ATTACHMENTS)),
    quality: 1,
    exif: false,
    preferredAssetRepresentationMode: ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Compatible,
  });
  if (res.canceled) return [];
  const at = new Date();
  const several = res.assets.length > 1;
  return res.assets.map((a, i) => {
    const real = a.fileName?.trim();
    const name = real && !isGeneratedFileName(real) ? real : readableName(a, at, several ? i + 1 : 0);
    return fromImageAsset(a, name);
  });
}

/** «الكاميرا» — one photo of a document, named by when it was taken. */
export async function takePhoto(): Promise<PickedFile[]> {
  const ImagePicker = await import("expo-image-picker");
  const current = await ImagePicker.getCameraPermissionsAsync();
  if (!current.granted) {
    const asked = current.canAskAgain ? await ImagePicker.requestCameraPermissionsAsync() : current;
    if (!asked.granted) throw new PickerDenied(MSG_CAMERA_DENIED);
  }
  const res = await ImagePicker.launchCameraAsync({ mediaTypes: ["images"], quality: 0.9, exif: false });
  if (res.canceled) return [];
  const at = new Date();
  return res.assets.map((a, i) => fromImageAsset(a, readableName(a, at, res.assets.length > 1 ? i + 1 : 0)));
}
