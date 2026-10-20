// Attachment limits + checks — the web's ChatInput.tsx:89-91, 838-851 and the
// backend's upload_session_service.py (ALLOWED_MIME_TYPES, MAX_FILE_SIZE,
// MAGIC_BYTES). Pure: no React Native, so `npm test` covers it.

export const MAX_ATTACHMENTS = 5;
export const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;

/** What the backend stores (finalize magic-byte checks exactly these three). */
export type UploadMime = "application/pdf" | "image/png" | "image/jpeg";
export const UPLOAD_MIMES: readonly UploadMime[] = ["application/pdf", "image/png", "image/jpeg"];

// Web texts, verbatim (ChatInput.tsx:839, 844, 848).
export const MSG_TOO_MANY = `الحد الأقصى ${MAX_ATTACHMENTS} ملفات`;
export const MSG_BAD_TYPE = "الملفات المقبولة: PDF، PNG، JPG فقط";
export const MSG_TOO_BIG = "الحد الأقصى لحجم الملف 50 ميجابايت";
// use-resumable-upload.ts:82 / :139 / :183.
export const MSG_UPLOAD_FAILED = "فشل رفع الملف. يرجى المحاولة مرة أخرى.";
export const MSG_SESSION_ENDED = "انتهت الجلسة. يرجى تسجيل الدخول مرة أخرى.";
export const MSG_BAD_RESPONSE = "استجابة غير صالحة من الخادم";
// Phone-only.
export const MSG_READ_FAILED = "تعذّرت قراءة الملف من الجهاز";
export const MSG_CONVERT_FAILED = "تعذّر تحويل الصورة — اختر صورة أخرى";
export const MSG_CAMERA_DENIED = "اسمح لريحان باستخدام الكاميرا من إعدادات الجهاز ثم أعد المحاولة";

/** A file the user picked, before any conversion. */
export interface PickedFile {
  uri: string;
  name: string;
  /** Picker-reported size; unknown (null) is measured after preparation. */
  size: number | null;
  /** Picker-reported MIME; may be missing or wrong — the bytes decide later. */
  mimeType: string | null;
}

export type PickedKind = "pdf" | "upload-image" | "convert-image" | "unsupported";

const IMAGE_EXT = /\.(heic|heif|webp|gif|bmp|tiff?|avif)$/i;

/** What a pick will become: kept as-is (PDF / PNG / JPEG), converted to JPEG
 *  (HEIC, WebP… — the backend accepts only the three), or refused. */
export function classifyPick(file: Pick<PickedFile, "name" | "mimeType">): PickedKind {
  const mime = (file.mimeType ?? "").toLowerCase();
  const name = file.name.toLowerCase();
  if (mime === "application/pdf" || (!mime && name.endsWith(".pdf"))) return "pdf";
  if (mime === "image/png" || mime === "image/jpeg" || mime === "image/jpg") return "upload-image";
  if (!mime && /\.(png|jpe?g)$/.test(name)) return "upload-image";
  if (mime.startsWith("image/") || (!mime && IMAGE_EXT.test(name))) return "convert-image";
  return "unsupported";
}

/** The web's up-front check of a whole selection (count, then each file's type
 *  and size). Any failure refuses the whole selection, like the web. */
export function validatePicks(currentCount: number, picks: readonly PickedFile[]): string | null {
  if (currentCount + picks.length > MAX_ATTACHMENTS) return MSG_TOO_MANY;
  for (const p of picks) {
    if (classifyPick(p) === "unsupported") return MSG_BAD_TYPE;
    if (p.size !== null && p.size > MAX_ATTACHMENT_BYTES) return MSG_TOO_BIG;
  }
  return null;
}

/** The real type from the first bytes (backend MAGIC_BYTES), or null. */
export function sniffMime(head: Uint8Array): UploadMime | null {
  if (head.length >= 4 && head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46) {
    return "application/pdf"; // %PDF
  }
  if (head.length >= 4 && head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47) {
    return "image/png"; // \x89PNG
  }
  if (head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return "image/jpeg";
  return null;
}

/** Message-bubble chip type (contracts `Attachment.attachment_type`). */
export function attachmentTypeFor(mime: string): "pdf" | "image" | "file" {
  if (mime === "application/pdf") return "pdf";
  return mime.startsWith("image/") ? "image" : "file";
}

/** Web AttachmentUploadCard formatBytes — Latin digits (toFixed never localises). */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** `IMG_1234.HEIC` → `IMG_1234.jpg` once converted. */
export function withJpegExtension(name: string): string {
  const base = name.replace(/\.[^./\\]+$/, "");
  return `${base || "صورة"}.jpg`;
}
