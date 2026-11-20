import { NextResponse } from "next/server";

/**
 * DEV ONLY — a minimal tus server for the «الملفات» preview's composer screen.
 * The preview's stubbed `/init` points uploads here, so the app's REAL
 * resumable upload (`runResumableUpload` → tus-js-client) runs end to end with
 * no backend and no storage. Bytes are counted, never kept. `?delay=<ms>`
 * slows each chunk so the "uploading" state can be watched (and answered).
 *
 * 404 outside `next dev`.
 */
export const dynamic = "force-dynamic";

interface Upload {
  length: number;
  offset: number;
  delay: number;
}

const uploads = new Map<string, Upload>();
const BASE = "/dev-preview/my-files/tus";
const ID_RE = /^[a-z0-9-]+$/;

function off(): NextResponse | null {
  return process.env.NODE_ENV === "development"
    ? null
    : new NextResponse(null, { status: 404 });
}

function tusHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { "Tus-Resumable": "1.0.0", "Cache-Control": "no-store", ...extra };
}

function delayOf(request: Request): number {
  const raw = Number(new URL(request.url).searchParams.get("delay") ?? "0");
  return Number.isFinite(raw) ? Math.min(Math.max(raw, 0), 10_000) : 0;
}

type Ctx = { params: Promise<{ slug?: string[] }> };

async function uploadId(ctx: Ctx): Promise<string | null> {
  const { slug } = await ctx.params;
  const id = slug?.[0] ?? "";
  return slug?.length === 1 && ID_RE.test(id) ? id : null;
}

/** Creation: `Upload-Length` in, `Location` out. */
export async function POST(request: Request, ctx: Ctx): Promise<NextResponse> {
  const blocked = off();
  if (blocked) return blocked;
  if (await uploadId(ctx)) return new NextResponse(null, { status: 404 });
  const length = Number(request.headers.get("Upload-Length") ?? "0");
  const id = `up-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const delay = delayOf(request);
  uploads.set(id, { length: Number.isFinite(length) ? length : 0, offset: 0, delay });
  return new NextResponse(null, {
    status: 201,
    headers: tusHeaders({ Location: `${BASE}/${id}?delay=${delay}` }),
  });
}

/** A chunk: counted, then acknowledged with the new offset. */
export async function PATCH(request: Request, ctx: Ctx): Promise<NextResponse> {
  const blocked = off();
  if (blocked) return blocked;
  const id = await uploadId(ctx);
  const upload = id ? uploads.get(id) : undefined;
  if (!upload) return new NextResponse(null, { status: 404, headers: tusHeaders() });
  const body = await request.arrayBuffer();
  if (upload.delay > 0) await new Promise((resolve) => setTimeout(resolve, upload.delay));
  upload.offset = Math.min(upload.length, upload.offset + body.byteLength);
  return new NextResponse(null, {
    status: 204,
    headers: tusHeaders({ "Upload-Offset": String(upload.offset) }),
  });
}

/** Resume probe. */
export async function HEAD(_request: Request, ctx: Ctx): Promise<NextResponse> {
  const blocked = off();
  if (blocked) return blocked;
  const id = await uploadId(ctx);
  const upload = id ? uploads.get(id) : undefined;
  if (!upload) return new NextResponse(null, { status: 404, headers: tusHeaders() });
  return new NextResponse(null, {
    status: 200,
    headers: tusHeaders({
      "Upload-Offset": String(upload.offset),
      "Upload-Length": String(upload.length),
    }),
  });
}

/** tus-js-client's abort(true) terminates the upload. */
export async function DELETE(_request: Request, ctx: Ctx): Promise<NextResponse> {
  const blocked = off();
  if (blocked) return blocked;
  const id = await uploadId(ctx);
  if (id) uploads.delete(id);
  return new NextResponse(null, { status: 204, headers: tusHeaders() });
}
