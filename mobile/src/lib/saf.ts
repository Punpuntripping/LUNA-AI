// Android Storage Access Framework helpers for the export «save to folder»
// flow. Pure string logic (no React Native) so `npm test` covers it.
//
// Why: on Android 11+ the folder picker (ACTION_OPEN_DOCUMENT_TREE) refuses the
// root of «Download» («Can't use this folder… choose another folder») — the
// user had to create a sub-folder first. «Documents» is NOT restricted, so the
// picker opens there (or in the folder the user chose last time), and «Use this
// folder» is enabled straight away: one tap to save.

const EXTERNAL_STORAGE_AUTHORITY = "com.android.externalstorage.documents";

/** The shared «Documents» folder on internal storage — selectable on Android 11+. */
export const ANDROID_DOCUMENTS_URI = `content://${EXTERNAL_STORAGE_AUTHORITY}/document/primary%3ADocuments`;

/**
 * A tree URI (what the picker returns) → the document URI of that same folder,
 * the form `DocumentsContract.EXTRA_INITIAL_URI` expects
 * (`DocumentsContract.buildDocumentUriUsingTree`). Null if it is not a tree URI.
 */
export function treeToDocumentUri(treeUri: string): string | null {
  const match = /^(content:\/\/[^/]+)\/tree\/([^/]+)\/?$/.exec(treeUri);
  if (!match) return null;
  const [, base, id] = match;
  return `${base}/tree/${id}/document/${id}`;
}

/** Where the folder picker opens: the last folder used, else «Documents». */
export function pickerStartUri(lastTreeUri: string | null): string {
  return (lastTreeUri && treeToDocumentUri(lastTreeUri)) ?? ANDROID_DOCUMENTS_URI;
}

/** The stored «last export folder» record (a small JSON file in the app's documents). */
export function serializeLastFolder(treeUri: string): string {
  return JSON.stringify({ treeUri });
}

/** Reads the record back; anything malformed or not a tree URI → null. */
export function parseLastFolder(raw: string): string | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const uri = (parsed as { treeUri?: unknown }).treeUri;
    return typeof uri === "string" && treeToDocumentUri(uri) !== null ? uri : null;
  } catch {
    return null;
  }
}
