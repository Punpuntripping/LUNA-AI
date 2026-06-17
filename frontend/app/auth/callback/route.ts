import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { createServerClient, type CookieOptions } from "@supabase/ssr";

/**
 * OAuth callback handler.
 *
 * Google (via Supabase) redirects here with a `?code=` after the user
 * approves sign-in. We exchange that code for a session server-side — this
 * writes the Supabase session cookies — then redirect to /chat, where
 * AuthGuard's loadUser() restores the session from the cookie into memory.
 *
 * The PKCE code verifier was stored as a cookie by the browser client when
 * signInWithOAuth() ran, so it is readable here on the same domain.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const oauthError = searchParams.get("error");

  // Behind Railway's proxy the request reaches the Next server on its internal
  // bind address, so `request.url`'s origin is `http://0.0.0.0:3000` — not a
  // browser-reachable URL. Prefer the public host/proto the proxy forwards so
  // our 302s land on rayhanai.com (or whichever domain the user came from).
  // Locally there's no proxy (no x-forwarded-host), so we fall back to origin.
  const forwardedHost = request.headers.get("x-forwarded-host");
  const forwardedProto = request.headers.get("x-forwarded-proto") ?? "https";
  const base =
    process.env.NODE_ENV === "development" || !forwardedHost
      ? origin
      : `${forwardedProto}://${forwardedHost}`;

  // User denied consent, or Supabase returned an error.
  if (oauthError || !code) {
    return NextResponse.redirect(`${base}/login?error=oauth`);
  }

  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(
          cookiesToSet: { name: string; value: string; options?: CookieOptions }[],
        ) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        },
      },
    },
  );

  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return NextResponse.redirect(`${base}/login?error=oauth`);
  }

  return NextResponse.redirect(`${base}/chat`);
}
