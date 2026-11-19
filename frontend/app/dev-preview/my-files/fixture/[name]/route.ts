import { promises as fs } from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";

/**
 * DEV ONLY — serves one fixture JSON for the «الملفات» preview harness
 * (`app/dev-preview/my-files`). The fixtures are API-shaped responses for one
 * real user's files and live OUTSIDE the repo, in `MY_FILES_FIXTURE_DIR`; they
 * are read on every request (never cached, never bundled), so a fixture that
 * gains `doc_type` / `sections` shows up on the next reload.
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

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  if (process.env.NODE_ENV !== "development") return notFound();
  const dir = process.env.MY_FILES_FIXTURE_DIR;
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
