import { NextResponse } from "next/server";

/**
 * Railway's deploy healthcheck for this service (`railway.json`,
 * `healthcheckPath: /api/v1/health`).
 *
 * ⚠ DO NOT DELETE. Railway probes this path on every deploy, and a 404 fails the
 * deploy. The development `/api/*` rewrite used to answer it by forwarding the
 * probe to the backend, so removing that rewrite from production failed the
 * frontend deploy on 2026-11-02. `middleware.ts` exempts this exact path from the
 * origin lock, because Railway probes the container directly and can never carry
 * the edge secret.
 *
 * Answers from this process and calls nothing else, so a backend or Cloudflare
 * problem cannot fail a frontend deploy.
 */
export const dynamic = "force-dynamic";

export function GET(): NextResponse {
  return NextResponse.json(
    { status: "ok", service: "luna-frontend" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
