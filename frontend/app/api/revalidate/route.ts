import { revalidatePath } from "next/cache";
import { NextResponse, type NextRequest } from "next/server";

/**
 * On-demand ISR revalidation endpoint. POST only.
 *
 * Contract (consumed by `scripts/set_gate.py`):
 *   Auth   → header `x-revalidate-secret` must equal `process.env.REVALIDATE_SECRET`
 *            · 503 if the env var is unset (misconfigured deploy)
 *            · 401 if the header is missing/wrong
 *   Body   → JSON `{"path": "/regulations/..."}` — `path` must start with "/"
 *            · 400 on missing/invalid path or unparseable body
 *   OK     → `revalidatePath(path)` then `{"revalidated": true, "path": path}`
 *
 * When `set_gate.py` flips an item's gate, it calls this so the affected page
 * re-renders from the new gated payload within seconds instead of waiting out
 * the ISR TTL.
 */
export async function POST(request: NextRequest) {
  const secret = process.env.REVALIDATE_SECRET;
  if (!secret) {
    return NextResponse.json(
      { revalidated: false, error: "خدمة إعادة التحقق غير مُهيّأة" },
      { status: 503 },
    );
  }

  if (request.headers.get("x-revalidate-secret") !== secret) {
    return NextResponse.json(
      { revalidated: false, error: "غير مصرّح" },
      { status: 401 },
    );
  }

  let path: unknown;
  try {
    const body = (await request.json()) as { path?: unknown };
    path = body.path;
  } catch {
    return NextResponse.json(
      { revalidated: false, error: "صيغة الطلب غير صالحة" },
      { status: 400 },
    );
  }

  if (typeof path !== "string" || !path.startsWith("/")) {
    return NextResponse.json(
      { revalidated: false, error: "المسار غير صالح" },
      { status: 400 },
    );
  }

  revalidatePath(path);

  return NextResponse.json({ revalidated: true, path });
}
