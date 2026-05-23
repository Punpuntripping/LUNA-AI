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

  // User denied consent, or Supabase returned an error.
  if (oauthError || !code) {
    return NextResponse.redirect(`${origin}/login?error=oauth`);
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
    return NextResponse.redirect(`${origin}/login?error=oauth`);
  }

  return NextResponse.redirect(`${origin}/chat`);
}
