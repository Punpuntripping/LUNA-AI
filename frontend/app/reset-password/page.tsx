import { redirect } from "next/navigation";

/**
 * Retired 2026-10-13 — passwords are now set with an emailed CODE on
 * /forgot-password (owner decision: email code only, no link).
 *
 * Reset links emailed before that date may still land here. This is a SERVER
 * redirect on purpose: nothing renders, no client code runs, and no recovery
 * session or token is ever touched. The query string is dropped; the only
 * thing carried over is the non-sensitive `link=expired` flag so the code page
 * can say why the visitor is there. (A `#fragment` survives any HTTP redirect —
 * /forgot-password strips it from the address bar on mount.)
 *
 * app/auth/callback short-circuits `next=/reset-password` straight to the same
 * place BEFORE exchanging the link's code, so a legacy link never signs anyone
 * in; this route only catches direct hits and bookmarked URLs.
 */
export default function ResetPasswordPage(): never {
  redirect("/forgot-password?link=expired");
}
