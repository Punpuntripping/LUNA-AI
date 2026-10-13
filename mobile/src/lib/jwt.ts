/** `exp` (seconds since epoch) from a JWT, or null. Reads the payload only —
 *  never verifies (the server does) and never logs the token. */
export function jwtExpiry(token: string): number | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const payload: unknown = JSON.parse(atob(padded));
    if (payload && typeof payload === "object" && "exp" in payload) {
      const exp = (payload as { exp: unknown }).exp;
      return typeof exp === "number" ? exp : null;
    }
    return null;
  } catch {
    return null;
  }
}
