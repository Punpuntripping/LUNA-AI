import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { createServerClient, type CookieOptions } from "@supabase/ssr";
import {
  DEFAULT_NEXT,
  IDENTITY_PARAM,
  isIdentityTag,
  nextForIdentity,
  safeNext,
} from "@/lib/safe-next";

/**
 * Auth callback handler — BOTH round trips land here.
 *
 * 1. Google OAuth: Supabase redirects here with `?code=` after the user
 *    approves sign-in.
 * 2. Email verification: GoTrue's /auth/v1/verify redirects here with `?code=`
 *    after the user clicks the confirmation link (`emailRedirectTo`).
 *
 * We exchange that code for a session server-side — this writes the Supabase
 * session cookies — then redirect to `next` (default /chat), where AuthGuard's
 * loadUser() restores the session from the cookie into memory.
 *
 * The PKCE code verifier was stored as a cookie by the browser client when
 * signInWithOAuth() / signUp() ran, so it is readable here on the same domain —
 * and only in that same browser (see the failure branch below).
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const oauthError = searchParams.get("error");

  // Return-to-page (anon_conversion_popup.md §7.2). Both `redirectTo` (OAuth)
  // and `emailRedirectTo` (signup) carry `?next=`; GoTrue preserves the query
  // it was given and appends its own `code`. The value is attacker-controlled,
  // so it is allowlisted here on read — trap T3.
  const rawNext = searchParams.get("next");
  const next = safeNext(rawNext);

  // `?u=` — the account `next` was minted for (lib/safe-next, IDENTITY_PARAM).
  // Google is a real account-switch surface: the visitor may well come back as
  // somebody else, and this handler is the only place that learns which uid the
  // code exchanged into. Shape-checked before it is echoed onto the failure
  // redirects below, since it arrives attacker-controlled exactly like `next`.
  const rawIdentity = searchParams.get(IDENTITY_PARAM);
  const identityQuery =
    isIdentityTag(rawIdentity)
      ? `&${IDENTITY_PARAM}=${encodeURIComponent(rawIdentity)}`
      : "";
  const nextQuery =
    next === DEFAULT_NEXT
      ? ""
      : `&next=${encodeURIComponent(next)}${identityQuery}`;

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

  // Google's own failure (consent denied, provider error) DOES arrive as a
  // query parameter, so this branch is real — and it is the only one that may
  // show the Google-specific message.
  if (oauthError) {
    return NextResponse.redirect(`${base}/login?error=oauth${nextQuery}`);
  }

  // No `code` at all. This is the failed email-confirmation shape: an expired
  // or already-used link reports its reason in the URL **fragment**
  // (`#error=access_denied&error_code=otp_expired`), which the browser never
  // transmits — so from here it is indistinguishable from any other codeless
  // arrival (trap T1b). Do not branch on `?error=` for it; it never comes.
  // Reason-neutral notice, `next` preserved so the manual login still returns
  // the reader to the page they were on (§7.4).
  if (!code) {
    return NextResponse.redirect(
      `${base}/login?notice=verify_elsewhere${nextQuery}`,
    );
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

  const { data, error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    // Almost always PKCE: the confirmation link was opened in a different
    // browser than the one that signed up, so the code_verifier cookie is not
    // here. Same reason-neutral notice as the codeless case — the account IS
    // confirmed, the visitor just has to sign in once (§7.4 / T12).
    return NextResponse.redirect(
      `${base}/login?notice=verify_elsewhere${nextQuery}`,
    );
  }

  // The session now exists, so `u` can finally be judged. Same account →
  // `next`; anyone else (a second Google account, a brand-new one) → /chat,
  // rather than onto a page belonging to whoever was signed in before.
  // Unscoped values are untouched: this returns `next` whenever no `u` rode
  // along, which is every anonymous conversion and every email confirmation.
  const landing = nextForIdentity(
    rawNext,
    rawIdentity,
    data.session?.user?.id ?? null,
  );

  return NextResponse.redirect(`${base}${landing}`);
}
