import { promises as fs } from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";

/**
 * DEV ONLY — serves one fixture JSON for the angle-map preview harness
 * (`app/dev-preview/angle-map`). The fixtures — written by
 * `scripts/angle_map_ui_fixture.py` from the angle-map preview runs — hold REAL
 * users' documents and live OUTSIDE the repo, in `ANGLE_MAP_FIXTURE_DIR`; they
 * are read on every request (never cached, never bundled).
 *
 * Answers 404 outside `next dev`, when the variable is unset, for any name that
 * is not `^[a-z0-9_-]+$` (so no path can be smuggled in), and for a missing
 * file.
 */
export const dynamic = "force-dynamic";

const NAME_RE = /^[a-z0-9_-]+$/;

function notFound(): NextResponse {
  return NextResponse.json(
    { detail: "غير موجود" },
    { status: 404, headers: { "Cache-Control": "no-store" } },
  );
}

/** The fixtures can be a real user's documents: answer the local machine only. */
function isLocalHost(request: Request): boolean {
  const host = (request.headers.get("host") ?? "").toLowerCase().replace(/:\d+$/, "");
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  if (process.env.NODE_ENV !== "development") return notFound();
  // Not reachable from another machine on the network, nor by a page that
  // re-binds a foreign name to this address. Run the server with
  // `next dev -H 127.0.0.1` as well.
  if (!isLocalHost(request)) return notFound();
  const dir = process.env.ANGLE_MAP_FIXTURE_DIR;
  const { name } = await params;
  if (!dir || !NAME_RE.test(name)) return notFound();

  try {
    const body = await fs.readFile(path.join(dir, `${name}.json`), "utf8");
    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return notFound();
  }
}
