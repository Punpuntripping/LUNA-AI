import { fetch as expoFetch } from "expo/fetch";
import { File, FileMode } from "expo-file-system";
import { AppState } from "react-native";

import { cancelAttachment, finalizeAttachment, initAttachment } from "@/api/attachments";
import { ApiError } from "@/api/errors";
import { refreshAccessToken, SessionExpiredError } from "@/auth/refresh";
import { useSession } from "@/auth/session-store";
import { getAccessToken } from "@/auth/token-store";
import { workspaceKeys } from "@/chat/keys";
import { queryClient } from "@/lib/query-client";

import {
  classifyPick,
  MAX_ATTACHMENT_BYTES,
  MSG_BAD_TYPE,
  MSG_CONVERT_FAILED,
  MSG_READ_FAILED,
  MSG_SESSION_ENDED,
  MSG_TOO_BIG,
  MSG_UPLOAD_FAILED,
  sniffMime,
  withJpegExtension,
  type PickedFile,
  type UploadMime,
} from "./limits";
import { createPdfPageCounter } from "./pdf-pages";
import { TusAbortedError, TusError, tusUpload, type TusDeps } from "./tus";
import { AttachmentFailure, createUploadManager, type PreparedFile, type UploadManager } from "./upload-manager";

// Production wiring of the attachment manager: expo-file-system byte ranges,
// expo-image-manipulator (HEIC/WebP → JPEG), expo/fetch for TUS, the app's
// session (bearer + single-flight refresh) and AppState (resume on `active`).

const JPEG_QUALITY = 0.9;
const SCAN_SLICE_BYTES = 1024 * 1024;

const abortError = () => new TusAbortedError();

function yieldToUi(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(abortError());
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

const isActive = () => AppState.currentState === "active";

function waitUntilActive(signal: AbortSignal): Promise<void> {
  if (isActive()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(abortError());
    const sub = AppState.addEventListener("change", (state) => {
      if (state !== "active") return;
      cleanup();
      resolve();
    });
    const onAbort = () => {
      cleanup();
      reject(abortError());
    };
    const cleanup = () => {
      sub.remove();
      signal.removeEventListener("abort", onAbort);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** Bytes [offset, offset + length) — never the whole file in memory. */
function readRange(uri: string, offset: number, length: number): Uint8Array {
  const handle = new File(uri).open(FileMode.ReadOnly);
  try {
    handle.offset = offset;
    return handle.readBytes(length);
  } finally {
    handle.close();
  }
}

async function countPages(uri: string, size: number, signal: AbortSignal): Promise<number> {
  const counter = createPdfPageCounter();
  try {
    const handle = new File(uri).open(FileMode.ReadOnly);
    try {
      for (let offset = 0; offset < size; offset += SCAN_SLICE_BYTES) {
        if (signal.aborted) throw abortError();
        handle.offset = offset;
        counter.feed(handle.readBytes(Math.min(SCAN_SLICE_BYTES, size - offset)));
        await yieldToUi();
      }
    } finally {
      handle.close();
    }
  } catch (err) {
    if (err instanceof TusAbortedError) throw err;
    // Estimate only: an unreadable scan falls back to the 1-page floor.
  }
  return counter.end();
}

async function toJpeg(uri: string): Promise<string> {
  try {
    // Lazy: a dev build without the native module fails here, not at start.
    const { ImageManipulator, SaveFormat } = await import("expo-image-manipulator");
    const image = await ImageManipulator.manipulate(uri).renderAsync();
    const saved = await image.saveAsync({ format: SaveFormat.JPEG, compress: JPEG_QUALITY });
    return saved.uri;
  } catch {
    throw new AttachmentFailure(MSG_CONVERT_FAILED);
  }
}

async function prepare(pick: PickedFile, signal: AbortSignal): Promise<PreparedFile> {
  const kind = classifyPick(pick);
  if (kind === "unsupported") throw new AttachmentFailure(MSG_BAD_TYPE);

  let head: Uint8Array;
  try {
    head = readRange(pick.uri, 0, 16);
  } catch {
    throw new AttachmentFailure(MSG_READ_FAILED);
  }
  // The bytes decide, not the picker's MIME / extension (the backend's
  // finalize magic-byte check would refuse a mismatch anyway).
  const sniffed = sniffMime(head);
  let uri = pick.uri;
  let name = pick.name;
  let mimeType: UploadMime;
  if (kind === "pdf") {
    if (sniffed !== "application/pdf") throw new AttachmentFailure(MSG_BAD_TYPE);
    mimeType = sniffed;
  } else if (sniffed === "image/png" || sniffed === "image/jpeg") {
    mimeType = sniffed;
    if (sniffed === "image/jpeg" && !/\.jpe?g$/i.test(name)) name = withJpegExtension(name);
  } else if (sniffed === "application/pdf") {
    throw new AttachmentFailure(MSG_BAD_TYPE);
  } else {
    // HEIC / WebP / GIF… → JPEG.
    uri = await toJpeg(pick.uri);
    name = withJpegExtension(name);
    mimeType = "image/jpeg";
  }
  if (signal.aborted) throw abortError();

  let size: number;
  try {
    size = new File(uri).size;
  } catch {
    throw new AttachmentFailure(MSG_READ_FAILED);
  }
  if (!size) throw new AttachmentFailure(MSG_READ_FAILED);
  if (size > MAX_ATTACHMENT_BYTES) throw new AttachmentFailure(MSG_TOO_BIG);

  const pageCount = mimeType === "application/pdf" ? await countPages(uri, size, signal) : 1;
  return { uri, name, size, mimeType, pageCount, previewUri: mimeType === "application/pdf" ? null : uri };
}

/** The chunk's own ArrayBuffer (readBytes returns a fresh one — no copy then). */
function exactBuffer(bytes: Uint8Array): ArrayBuffer {
  const whole = bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength;
  return (whole ? bytes.buffer : bytes.slice().buffer) as ArrayBuffer;
}

function tusDeps(uri: string): TusDeps {
  return {
    async send(req) {
      const res = await expoFetch(req.url, {
        method: req.method,
        headers: req.headers,
        // A standalone ArrayBuffer of exactly this chunk (expo/fetch BodyInit).
        body: req.body ? exactBuffer(req.body) : undefined,
        signal: req.signal,
      });
      return { status: res.status, header: (name) => res.headers.get(name) };
    },
    readChunk: async (offset, length) => readRange(uri, offset, length),
    getToken: getAccessToken,
    refreshToken: refreshAccessToken,
    sleep,
    isActive,
    waitUntilActive,
  };
}

const LEAKY = /(https?:\/\/|cases\/|general\/|convos\/)|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const ARABIC = /[؀-ۿ]/;

/** Web arabicErrorFromUnknown (use-resumable-upload.ts:60-83): only a short
 *  Arabic backend message without paths / ids is shown; else the fallback. */
export function uploadErrorText(err: unknown): string {
  if (err instanceof AttachmentFailure) return err.message;
  if (err instanceof SessionExpiredError) return MSG_SESSION_ENDED;
  if (err instanceof TusError && (err.status === 401 || err.status === 403)) return MSG_SESSION_ENDED;
  if (err instanceof ApiError) {
    const msg = err.message ?? "";
    if (msg && msg.length <= 200 && ARABIC.test(msg) && !LEAKY.test(msg)) return msg;
  }
  return MSG_UPLOAD_FAILED;
}

export const attachmentUploads: UploadManager = createUploadManager({
  prepare,
  initAttachment,
  async upload(prepared, init, signal, onProgress) {
    await tusUpload(tusDeps(prepared.uri), {
      endpoint: init.upload_url,
      size: prepared.size,
      signal,
      metadata: {
        bucketName: init.bucket,
        objectName: init.storage_path,
        contentType: prepared.mimeType,
        cacheControl: "3600",
      },
      onProgress: (sent, total) => onProgress(total > 0 ? sent / total : 0),
    });
  },
  finalizeAttachment,
  cancelAttachment,
  errorText: uploadErrorText,
  whenActive: waitUntilActive,
  onCompleted: (conversationId) => {
    void queryClient.invalidateQueries({ queryKey: workspaceKeys.byConversation(conversationId) });
  },
});

// Nothing of one account may survive into the next (session-store signedOut).
useSession.subscribe((state, previous) => {
  if (state.status === "signedOut" && previous.status !== "signedOut") attachmentUploads.reset();
});
