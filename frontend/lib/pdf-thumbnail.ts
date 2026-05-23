/**
 * Client-side PDF thumbnail generator.
 *
 * Lazy-loads pdfjs-dist (~300 KB) only when the user actually attaches a
 * PDF — users who never attach a PDF pay zero bundle cost. The worker is
 * pulled from a CDN so we don't have to copy a worker file into /public
 * and keep its version in sync with the package version.
 *
 * Renders page 1 of the PDF to a canvas and returns a data URL the
 * AttachmentUploadCard can drop straight into an <img>. On any failure
 * (encrypted PDF, malformed bytes, worker fetch blocked) returns null and
 * the caller falls back to the generic FileText icon.
 */
"use client";

// pdfjs-dist's `version` export is a runtime string we use to pin the worker
// URL to the same minor as the package — avoids API/worker drift after a
// future `npm update`.
type PdfJsModule = typeof import("pdfjs-dist");

let pdfjsPromise: Promise<PdfJsModule> | null = null;

async function loadPdfjs(): Promise<PdfJsModule> {
  if (pdfjsPromise) return pdfjsPromise;
  pdfjsPromise = (async () => {
    const pdfjs = await import("pdfjs-dist");
    // jsdelivr mirrors the same version of pdfjs-dist's worker bundle; the
    // template literal binds the worker version to the npm version of the
    // library we just loaded so upgrades stay in lockstep.
    pdfjs.GlobalWorkerOptions.workerSrc = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;
    return pdfjs;
  })();
  return pdfjsPromise;
}

export interface PdfThumbnailOptions {
  /** Target thumbnail width in CSS pixels. Default 240 (covers @2x retina). */
  maxWidth?: number;
}

/**
 * Render page 1 of `file` to a PNG data URL.
 *
 * Returns null when the file isn't a PDF, the browser can't render it, or
 * any error is thrown — callers should fall back to a generic icon.
 */
export async function generatePdfThumbnail(
  file: File,
  options: PdfThumbnailOptions = {},
): Promise<string | null> {
  if (file.type !== "application/pdf") return null;
  if (typeof document === "undefined") return null; // SSR safety.

  const maxWidth = options.maxWidth ?? 240;

  try {
    const pdfjs = await loadPdfjs();
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await pdfjs.getDocument({ data: arrayBuffer }).promise;
    const page = await pdf.getPage(1);

    // Two-pass viewport: probe to find the natural width at scale=1, then
    // re-scale so the rendered canvas is `maxWidth` CSS pixels wide. This
    // keeps thumbnails consistent across A4 / letter / scanned-image PDFs.
    const naturalViewport = page.getViewport({ scale: 1 });
    const scale = maxWidth / naturalViewport.width;
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    await page.render({ canvas, canvasContext: ctx, viewport }).promise;
    return canvas.toDataURL("image/png");
  } catch (err) {
    // Encrypted / damaged / worker-blocked PDFs land here. Caller renders
    // the generic FileText icon when null is returned.
    if (typeof console !== "undefined") {
      console.warn("[pdf-thumbnail]", err);
    }
    return null;
  }
}
