// Public build-time config. Expo inlines `process.env.EXPO_PUBLIC_*` at bundle
// time, so each variable must be read with its full literal name (no dynamic
// lookup). Never put a secret behind an EXPO_PUBLIC_ name — it ships in the app.

const DEFAULT_API_URL = "https://api.rayhanai.com";

function cleanUrl(value: string | undefined, fallback: string): string {
  const v = (value ?? "").trim();
  return (v || fallback).replace(/\/+$/, "");
}

/** API origin. Always the Cloudflare-fronted host — never `*.up.railway.app`
 *  (the origin lock 403s direct hits once EDGE_SECRET is armed). */
export const API_URL = cleanUrl(process.env.EXPO_PUBLIC_API_URL, DEFAULT_API_URL);

export const API_PREFIX = "/api/v1";

/** Public web origin — legal pages open here in the in-app browser. */
export const WEB_URL = "https://rayhanai.com";
