/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { UploadInitResponse, WorkspaceItem } from "@contracts";

import {
  classifyPick,
  formatBytes,
  imageExtension,
  isGeneratedFileName,
  MAX_ATTACHMENT_BYTES,
  MSG_BAD_TYPE,
  MSG_TOO_BIG,
  MSG_TOO_MANY,
  photoFileName,
  sniffMime,
  validatePicks,
  withJpegExtension,
  type PickedFile,
} from "../limits";
import { countPdfPages, createPdfPageCounter } from "../pdf-pages";
import { base64Utf8, encodeMetadata, resolveLocation, TusAbortedError, TusError, tusUpload, type TusDeps, type TusRequest } from "../tus";
import { AttachmentFailure, attachmentGate, createUploadManager, type PreparedFile, type UploadManagerDeps } from "../upload-manager";

const enc = new TextEncoder();
const tick = () => new Promise((r) => setTimeout(r, 0));

// ---------------------------------------------------------------- limits

const pick = (name: string, mimeType: string | null, size: number | null = 1000): PickedFile => ({
  uri: `file:///${name}`,
  name,
  mimeType,
  size,
});

describe("limits", () => {
  test("classifies picks like the backend's three types (+ convertible images)", () => {
    assert.equal(classifyPick(pick("a.pdf", "application/pdf")), "pdf");
    assert.equal(classifyPick(pick("a.pdf", null)), "pdf");
    assert.equal(classifyPick(pick("a.png", "image/png")), "upload-image");
    assert.equal(classifyPick(pick("a.jpg", "image/jpg")), "upload-image");
    assert.equal(classifyPick(pick("IMG.HEIC", "image/heic")), "convert-image");
    assert.equal(classifyPick(pick("x.webp", null)), "convert-image");
    assert.equal(classifyPick(pick("a.docx", "application/vnd.openxmlformats-officedocument")), "unsupported");
  });

  test("validates a selection with the web's texts (count first, then type, size)", () => {
    assert.equal(validatePicks(0, [pick("a.pdf", "application/pdf")]), null);
    assert.equal(validatePicks(4, [pick("a.pdf", "application/pdf"), pick("b.pdf", "application/pdf")]), MSG_TOO_MANY);
    assert.equal(MSG_TOO_MANY, "الحد الأقصى 5 ملفات");
    assert.equal(validatePicks(0, [pick("a.pdf", "application/pdf"), pick("a.zip", "application/zip")]), MSG_BAD_TYPE);
    assert.equal(validatePicks(0, [pick("a.pdf", "application/pdf", MAX_ATTACHMENT_BYTES + 1)]), MSG_TOO_BIG);
    assert.equal(validatePicks(0, [pick("a.pdf", "application/pdf", MAX_ATTACHMENT_BYTES)]), null);
    assert.equal(validatePicks(0, [pick("a.jpg", "image/jpeg", null)]), null, "unknown size is measured later");
  });

  test("sniffs magic bytes like the backend's finalize check", () => {
    assert.equal(sniffMime(enc.encode("%PDF-1.7")), "application/pdf");
    assert.equal(sniffMime(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d])), "image/png");
    assert.equal(sniffMime(new Uint8Array([0xff, 0xd8, 0xff, 0xe0])), "image/jpeg");
    assert.equal(sniffMime(enc.encode("....ftypheic")), null);
    assert.equal(sniffMime(new Uint8Array([0x25])), null);
  });

  test("names and sizes", () => {
    assert.equal(withJpegExtension("IMG_1234.HEIC"), "IMG_1234.jpg");
    assert.equal(withJpegExtension("scan"), "scan.jpg");
    assert.equal(formatBytes(512), "512 B");
    assert.equal(formatBytes(2048), "2.0 KB");
    assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MB");
  });

  test("photo names: picker UUIDs are not names; dated Arabic name with Latin digits (QA F9)", () => {
    assert.equal(isGeneratedFileName("2d0394d3-5e2f-4c1a-9b7e-0123456789ab.jpeg"), true);
    assert.equal(isGeneratedFileName("2D0394D3-5E2F-4C1A-9B7E-0123456789AB"), true);
    assert.equal(isGeneratedFileName(""), true);
    assert.equal(isGeneratedFileName(null), true);
    assert.equal(isGeneratedFileName("IMG_1234.HEIC"), false);
    assert.equal(isGeneratedFileName("عقد الإيجار.jpg"), false);

    const at = new Date(2026, 9, 5, 9, 3); // local time, months 0-based
    assert.equal(photoFileName(at, ".jpeg"), "صورة 2026-10-05 09-03.jpeg");
    assert.equal(photoFileName(at, ".jpg", 2), "صورة 2026-10-05 09-03 (2).jpg");
    assert.match(photoFileName(at, ".jpeg"), /^[^٠-٩:]*$/); // no Arabic-Indic digits, no «:»

    assert.equal(imageExtension("file:///cache/2d0394d3-5e2f-4c1a-9b7e-0123456789ab.JPEG", null), ".jpeg");
    assert.equal(imageExtension("IMG_1.HEIC", "image/heic"), ".heic");
    assert.equal(imageExtension("content://media/123", "image/png"), ".png");
    assert.equal(imageExtension(null, null), ".jpeg");
    // A converted HEIC keeps the readable stem.
    assert.equal(withJpegExtension(photoFileName(at, ".heic")), "صورة 2026-10-05 09-03.jpg");
  });
});

// ---------------------------------------------------------------- page count

const PDF = [
  "%PDF-1.4",
  "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
  "2 0 obj << /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >> endobj",
  "3 0 obj << /Type /Page /Parent 2 0 R >> endobj",
  "4 0 obj <</Type/Page/Parent 2 0 R>> endobj",
  "5 0 obj << /Type\n/Page\r\n/Parent 2 0 R >> endobj",
  "6 0 obj << /Type /PageLabel >> endobj",
  "%%EOF",
].join("\n");

describe("pdf page estimate", () => {
  test("counts /Type /Page objects, never /Pages or /PageLabel", () => {
    assert.equal(countPdfPages(enc.encode(PDF)), 3);
  });

  test("no page objects visible (compressed object streams) → 1-page floor", () => {
    assert.equal(countPdfPages(enc.encode("%PDF-1.7 /Type /ObjStm stream xyz endstream")), 1);
    assert.equal(countPdfPages(new Uint8Array(0)), 1);
  });

  test("a match split across slices is counted exactly once, at every split point", () => {
    const bytes = enc.encode(PDF);
    for (let cut = 1; cut < bytes.length; cut++) {
      const counter = createPdfPageCounter();
      counter.feed(bytes.subarray(0, cut));
      counter.feed(bytes.subarray(cut));
      assert.equal(counter.end(), 3, `cut at ${cut}`);
    }
  });

  test("tiny slices (1 byte) still count right", () => {
    const bytes = enc.encode(PDF);
    const counter = createPdfPageCounter();
    for (const b of bytes) counter.feed(new Uint8Array([b]));
    assert.equal(counter.end(), 3);
  });

  test("/Type /Page at the very end of the file counts", () => {
    assert.equal(countPdfPages(enc.encode("x /Type /Page /Type /Page")), 2);
  });
});

// ---------------------------------------------------------------- TUS

interface FakeServer {
  deps: TusDeps;
  requests: TusRequest[];
  /** Bytes the server holds. */
  offset: () => number;
  /** Fail the next N requests of a method with a status (0 = network error). */
  failNext(method: TusRequest["method"], status: number, times?: number): void;
  setActive(active: boolean): void;
  refreshes: () => number;
  sleeps: number[];
}

function fakeServer(size: number, opts: { location?: string; token?: string | null } = {}): FakeServer {
  let held = 0;
  let active = true;
  let refreshes = 0;
  let token = opts.token === undefined ? "t1" : opts.token;
  const activeWaiters: (() => void)[] = [];
  const failures: { method: string; status: number }[] = [];
  const requests: TusRequest[] = [];
  const sleeps: number[] = [];
  const header = (h: Record<string, string>) => (name: string) => h[name.toLowerCase()] ?? null;
  const deps: TusDeps = {
    async send(req) {
      requests.push(req);
      const failIdx = failures.findIndex((f) => f.method === req.method);
      if (failIdx >= 0) {
        const [f] = failures.splice(failIdx, 1);
        if (f.status === 0) throw new Error("network");
        return { status: f.status, header: header({}) };
      }
      if (req.headers.Authorization !== `Bearer ${token}`) return { status: 401, header: header({}) };
      if (req.method === "POST") return { status: 201, header: header({ location: opts.location ?? "/upload/resumable/abc" }) };
      if (req.method === "HEAD") return { status: 200, header: header({ "upload-offset": String(held) }) };
      if (Number(req.headers["Upload-Offset"]) !== held) return { status: 409, header: header({}) };
      held += req.body?.length ?? 0;
      return { status: 204, header: header({ "upload-offset": String(held) }) };
    },
    async readChunk(offset, length) {
      return new Uint8Array(Math.max(0, Math.min(length, size - offset)));
    },
    getToken: () => token,
    async refreshToken() {
      refreshes++;
      token = "t2";
      return token;
    },
    async sleep(ms) {
      sleeps.push(ms);
    },
    isActive: () => active,
    waitUntilActive: () => new Promise<void>((resolve) => activeWaiters.push(resolve)),
  };
  return {
    deps,
    requests,
    sleeps,
    offset: () => held,
    failNext(method, status, times = 1) {
      for (let i = 0; i < times; i++) failures.push({ method, status });
    },
    setActive(value) {
      active = value;
      if (value) activeWaiters.splice(0).forEach((r) => r());
    },
    refreshes: () => refreshes,
  };
}

const ENDPOINT = "https://x.supabase.co/storage/v1/upload/resumable";
const META = { bucketName: "documents", objectName: "general/u/convos/c/ab_file.pdf", contentType: "application/pdf", cacheControl: "3600" };

describe("tus", () => {
  test("base64 metadata (UTF-8) + location resolution", () => {
    assert.equal(base64Utf8("documents"), "ZG9jdW1lbnRz");
    assert.equal(base64Utf8("ab"), "YWI=");
    assert.equal(base64Utf8("ملف"), Buffer.from("ملف", "utf8").toString("base64"));
    assert.equal(encodeMetadata({ a: "x", b: "yz" }), "a eA==,b eXo=");
    assert.equal(resolveLocation(ENDPOINT, "/storage/v1/upload/resumable/id1"), "https://x.supabase.co/storage/v1/upload/resumable/id1");
    assert.equal(resolveLocation(ENDPOINT, "https://y.co/u/1"), "https://y.co/u/1");
  });

  test("creates, then PATCHes 6 MB-aligned chunks with the web's headers", async () => {
    const size = 13 * 1024 * 1024;
    const srv = fakeServer(size);
    const progress: number[] = [];
    const res = await tusUpload(srv.deps, {
      endpoint: ENDPOINT,
      size,
      metadata: META,
      signal: new AbortController().signal,
      onProgress: (sent) => progress.push(sent),
    });
    assert.equal(srv.offset(), size);
    assert.equal(res.uploadUrl, "https://x.supabase.co/upload/resumable/abc");
    const [create, ...patches] = srv.requests;
    assert.equal(create.method, "POST");
    assert.equal(create.headers["Tus-Resumable"], "1.0.0");
    assert.equal(create.headers["x-upsert"], "true");
    assert.equal(create.headers["Upload-Length"], String(size));
    assert.match(create.headers["Upload-Metadata"], /^bucketName ZG9jdW1lbnRz,objectName /);
    assert.deepEqual(
      patches.map((p) => [p.headers["Upload-Offset"], p.body?.length]),
      [
        ["0", 6 * 1024 * 1024],
        [String(6 * 1024 * 1024), 6 * 1024 * 1024],
        [String(12 * 1024 * 1024), 1024 * 1024],
      ],
    );
    assert.equal(patches[0].headers["Content-Type"], "application/offset+octet-stream");
    assert.deepEqual(progress, [0, 6 * 1024 * 1024, 12 * 1024 * 1024, size]);
  });

  test("a failed PATCH → retry after HEAD resync, from the server's offset", async () => {
    const size = 13 * 1024 * 1024;
    const srv = fakeServer(size);
    srv.failNext("PATCH", 0); // first chunk lost
    await tusUpload(srv.deps, { endpoint: ENDPOINT, size, metadata: META, signal: new AbortController().signal });
    assert.equal(srv.offset(), size);
    const methods = srv.requests.map((r) => r.method);
    assert.deepEqual(methods, ["POST", "PATCH", "HEAD", "PATCH", "PATCH", "PATCH"]);
    assert.deepEqual(srv.sleeps, [0]);
  });

  test("gives up after the web's 5 retry delays", async () => {
    const srv = fakeServer(100);
    srv.failNext("POST", 503, 6);
    await assert.rejects(
      tusUpload(srv.deps, { endpoint: ENDPOINT, size: 100, metadata: META, signal: new AbortController().signal }),
      (err: unknown) => err instanceof TusError && err.status === 503,
    );
    assert.deepEqual(srv.sleeps, [0, 1_000, 3_000, 5_000, 10_000]);
  });

  test("a 4xx other than auth/409/423/429 is fatal at once", async () => {
    const srv = fakeServer(100);
    srv.failNext("POST", 413);
    await assert.rejects(
      tusUpload(srv.deps, { endpoint: ENDPOINT, size: 100, metadata: META, signal: new AbortController().signal }),
      (err: unknown) => err instanceof TusError && err.fatal,
    );
    assert.deepEqual(srv.sleeps, []);
  });

  test("an expired token → one refresh, then the upload continues with the new one", async () => {
    const srv = fakeServer(100);
    srv.failNext("PATCH", 401);
    await tusUpload(srv.deps, { endpoint: ENDPOINT, size: 100, metadata: META, signal: new AbortController().signal });
    assert.equal(srv.refreshes(), 1);
    assert.equal(srv.offset(), 100);
    assert.equal(srv.requests.at(-1)?.headers.Authorization, "Bearer t2");
  });

  test("backgrounded mid-upload: parks without using retries, resumes from the HEAD offset on active", async () => {
    const size = 13 * 1024 * 1024;
    const srv = fakeServer(size);
    const ctrl = new AbortController();
    // First chunk lands, then the OS kills the socket while in background.
    let patches = 0;
    const send = srv.deps.send;
    srv.deps.send = async (req) => {
      if (req.method === "PATCH" && ++patches === 2) {
        srv.setActive(false);
        throw new Error("socket closed");
      }
      return send(req);
    };
    const done = tusUpload(srv.deps, { endpoint: ENDPOINT, size, metadata: META, signal: ctrl.signal });
    await tick();
    await tick();
    assert.equal(srv.offset(), 6 * 1024 * 1024, "parked after the first chunk");
    const parkedAt = srv.requests.length;
    srv.setActive(true);
    await done;
    assert.equal(srv.offset(), size);
    assert.equal(srv.requests[parkedAt].method, "HEAD");
    assert.equal(srv.requests[parkedAt + 1].headers["Upload-Offset"], String(6 * 1024 * 1024));
    assert.deepEqual(srv.sleeps, [], "background wait spends no retry");
  });

  test("abort rejects with TusAbortedError and sends nothing more", async () => {
    const srv = fakeServer(100);
    const ctrl = new AbortController();
    srv.setActive(false);
    srv.failNext("POST", 0);
    const done = tusUpload(srv.deps, { endpoint: ENDPOINT, size: 100, metadata: META, signal: ctrl.signal });
    await tick();
    ctrl.abort();
    srv.setActive(true);
    await assert.rejects(done, (err: unknown) => err instanceof TusAbortedError);
    assert.equal(srv.requests.length, 1);
  });

  test("no session → fatal before any request", async () => {
    const srv = fakeServer(100, { token: null });
    await assert.rejects(tusUpload(srv.deps, { endpoint: ENDPOINT, size: 100, metadata: META, signal: new AbortController().signal }));
    assert.equal(srv.requests.length, 0);
  });
});

// ---------------------------------------------------------------- manager

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (e: unknown) => void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function managerHarness() {
  const log: string[] = [];
  const uploads: Deferred<void>[] = [];
  let item = 0;
  let id = 0;
  let initHold: Deferred<void> | null = null;
  const deps: UploadManagerDeps = {
    async prepare(p): Promise<PreparedFile> {
      if (p.name === "bad.heic") throw new AttachmentFailure("تعذّر تحويل الصورة — اختر صورة أخرى");
      return { uri: p.uri, name: p.name, size: 2000, mimeType: "application/pdf", pageCount: 4, previewUri: null };
    },
    async initAttachment(conversationId, body) {
      if (initHold) await initHold.promise;
      item++;
      log.push(`init:${conversationId}:${body.filename}:${body.page_count}`);
      return { item_id: `wi${item}`, storage_path: "p", bucket: "b", upload_url: "u", expires_at: "" } as UploadInitResponse;
    },
    upload(_p, init, signal, onProgress) {
      const d = deferred<void>();
      uploads.push(d);
      onProgress(0.5);
      signal.addEventListener("abort", () => d.reject(new TusAbortedError()));
      log.push(`upload:${init.item_id}`);
      return d.promise;
    },
    async finalizeAttachment(itemId) {
      log.push(`finalize:${itemId}`);
      return { item_id: itemId } as WorkspaceItem;
    },
    async cancelAttachment(itemId) {
      log.push(`cancel:${itemId}`);
      return { success: true };
    },
    errorText: (err) => (err instanceof AttachmentFailure ? err.message : "فشل رفع الملف. يرجى المحاولة مرة أخرى."),
    whenActive: async () => undefined,
    onCompleted: (c) => log.push(`completed:${c}`),
    newId: () => `chip${++id}`,
  };
  const holdInit = () => {
    initHold = deferred<void>();
    return initHold;
  };
  return { manager: createUploadManager(deps), log, uploads, holdInit };
}

describe("upload manager", () => {
  test("pick → init (with page_count) → upload → finalize → completed; send blocked meanwhile", async () => {
    const { manager, log, uploads } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf")]);
    assert.equal(manager.hasInFlight("c1"), true);
    await tick();
    assert.equal(manager.list("c1")[0].status, "uploading");
    assert.equal(manager.list("c1")[0].progress, 0.5);
    assert.deepEqual(attachmentGate(manager.list("c1")), { uploading: true, sendable: 0 });

    uploads[0].resolve();
    await tick();
    await tick();
    const [chip] = manager.list("c1");
    assert.equal(chip.status, "completed");
    assert.equal(chip.itemId, "wi1");
    assert.equal(manager.hasInFlight("c1"), false);
    assert.equal(manager.sendableCount("c1"), 1);
    assert.deepEqual(log, ["init:c1:a.pdf:4", "upload:wi1", "finalize:wi1", "completed:c1"]);
  });

  test("takeForSend: ids + optimistic chips; chips stay until release (= message_start)", async () => {
    const { manager, uploads } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf"), pick("bad.heic", "image/heic")]);
    await tick();
    uploads[0].resolve();
    await tick();
    await tick();
    assert.deepEqual(
      manager.list("c1").map((c) => c.status),
      ["completed", "failed"],
    );
    const send = manager.takeForSend("c1");
    assert.deepEqual(send.attachmentIds, ["wi1"]);
    assert.deepEqual(send.optimisticAttachments, [
      { id: "chip1", document_id: "wi1", attachment_type: "pdf", filename: "a.pdf", file_size: 2000, kind: "attachment" },
    ]);
    // A refused send never calls release → the chips are still there.
    assert.equal(manager.list("c1").length, 2);
    // Meanwhile the user attaches another file: release must not eat it.
    manager.add("c1", [pick("b.pdf", "application/pdf")]);
    send.release();
    assert.deepEqual(
      manager.list("c1").map((c) => c.name),
      ["b.pdf"],
    );
  });

  test("conversations are isolated", async () => {
    const { manager } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf")]);
    manager.add("c2", [pick("b.pdf", "application/pdf")]);
    assert.equal(manager.list("c1").length, 1);
    assert.equal(manager.takeForSend("c2").attachmentIds.length, 0);
    assert.equal(manager.list("c2")[0].name, "b.pdf");
  });

  test("remove mid-upload aborts and cancels on the server; a completed chip is only dropped", async () => {
    const { manager, log, uploads } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf"), pick("b.pdf", "application/pdf")]);
    await tick();
    uploads[1].resolve();
    await tick();
    await tick();
    manager.remove("c1", "chip1"); // uploading
    manager.remove("c1", "chip2"); // completed
    await tick();
    assert.equal(manager.list("c1").length, 0);
    assert.ok(log.includes("cancel:wi1"));
    assert.ok(!log.includes("cancel:wi2"), "a completed attachment stays in the workspace (web)");
    assert.ok(!log.includes("finalize:wi1"));
  });

  test("remove while /init is in flight cancels the row once it lands", async () => {
    const { manager, log, holdInit } = managerHarness();
    const hold = holdInit();
    manager.add("c1", [pick("a.pdf", "application/pdf")]);
    await tick(); // prepared; /init in flight
    manager.remove("c1", "chip1");
    hold.resolve();
    await tick();
    await tick();
    assert.equal(manager.list("c1").length, 0);
    assert.deepEqual(log, ["init:c1:a.pdf:4", "cancel:wi1"]);
  });

  test("failed → «إعادة المحاولة» cancels the failed row and starts over with a new init", async () => {
    const { manager, log, uploads } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf")]);
    await tick();
    uploads[0].reject(new Error("boom"));
    await tick();
    const failed = manager.list("c1")[0];
    assert.equal(failed.status, "failed");
    assert.equal(failed.error, "فشل رفع الملف. يرجى المحاولة مرة أخرى.");
    manager.retry("c1", "chip1");
    await tick();
    assert.equal(manager.list("c1")[0].status, "uploading");
    uploads[1].resolve();
    await tick();
    await tick();
    assert.equal(manager.list("c1")[0].status, "completed");
    assert.equal(manager.list("c1")[0].itemId, "wi2");
    assert.ok(log.includes("cancel:wi1"));
  });

  test("a refused preparation shows its own Arabic text", async () => {
    const { manager } = managerHarness();
    manager.add("c1", [pick("bad.heic", "image/heic")]);
    await tick();
    assert.equal(manager.list("c1")[0].error, "تعذّر تحويل الصورة — اختر صورة أخرى");
  });

  test("remove while preparing never opens a row", async () => {
    const { manager, log } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf")]);
    manager.remove("c1", "chip1");
    await tick();
    await tick();
    assert.deepEqual(log, []);
  });

  test("reset (sign-out) aborts and forgets everything", async () => {
    const { manager, log } = managerHarness();
    manager.add("c1", [pick("a.pdf", "application/pdf")]);
    await tick();
    manager.reset();
    await tick();
    assert.deepEqual(manager.store.getState().byConversation, {});
    assert.ok(!log.some((l) => l.startsWith("finalize")));
  });
});
