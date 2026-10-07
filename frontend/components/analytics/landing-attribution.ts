/**
 * Signup source — the landing's `utm_source` / `utm_campaign` and, for X ads,
 * X's click id `twclid` (marketing request `request_to_luna_x_conversions.md`
 * X1–X3, backend migration 172).
 *
 * Captured on landing, carried through signup (`auth-store.register` puts it in
 * `signUp` metadata so it survives a confirmation link opened in another
 * browser), and sent once by `SignupCompletedTracker` to
 * `POST /attribution/signup`. The backend is the only party that ever talks to
 * X, and the ONLY user value it sends is `twclid`. utm values never go to X.
 *
 * ⚠ sessionStorage, never localStorage or a cookie — same posture as
 * `signup-attribution.ts`: a VISIT is tracked, a PERSON is not. No X pixel, no
 * X script.
 *
 * ⚠ FAILS CLOSED and never throws: unusable storage means an unattributed
 * signup, which is still a signup.
 *
 * ⚠ `twclid` arrives on a URL, so it is attacker-controlled: shape-checked
 * here and again on the server (and by a CHECK constraint in 172).
 */

export const LANDING_ATTRIBUTION_STORAGE_KEY = "rayhan_x_twclid_v1";

export interface LandingAttribution {
  twclid?: string;
  utm_source?: string;
  utm_campaign?: string;
}

const TWCLID_RE = /^[A-Za-z0-9_-]{1,200}$/;
const UTM_MAX = 120;

function cleanTwclid(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const v = value.trim();
  return TWCLID_RE.test(v) ? v : undefined;
}

function cleanUtm(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const v = value.trim();
  // Printable ASCII only — the same rule the backend applies.
  if (!v || !/^[\x20-\x7E]+$/.test(v)) return undefined;
  return v.slice(0, UTM_MAX);
}

function cleaned(raw: Record<string, unknown>): LandingAttribution {
  const out: LandingAttribution = {};
  const twclid = cleanTwclid(raw.twclid);
  const utmSource = cleanUtm(raw.utm_source);
  const utmCampaign = cleanUtm(raw.utm_campaign);
  if (twclid) out.twclid = twclid;
  if (utmSource) out.utm_source = utmSource;
  if (utmCampaign) out.utm_campaign = utmCampaign;
  return out;
}

/**
 * Read the landing URL and remember its source. Only a URL that CARRIES a
 * source overwrites the stash, and it replaces it as a set: the latest ad
 * click wins, and an X click id never sticks to a later non-X campaign.
 */
export function captureLandingAttribution(): void {
  try {
    const params = new URLSearchParams(window.location.search);
    const found = cleaned({
      twclid: params.get("twclid"),
      utm_source: params.get("utm_source"),
      utm_campaign: params.get("utm_campaign"),
    });
    if (!Object.keys(found).length) return;
    window.sessionStorage.setItem(
      LANDING_ATTRIBUTION_STORAGE_KEY,
      JSON.stringify(found),
    );
  } catch {
    // T9 — attribution is a nice-to-have.
  }
}

/** The stashed source, or `{}`. Not cleared on read (the server is write-once). */
export function readLandingAttribution(): LandingAttribution {
  try {
    const raw = window.sessionStorage.getItem(LANDING_ATTRIBUTION_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    return cleaned(parsed as Record<string, unknown>);
  } catch {
    return {};
  }
}

/**
 * The same values under the `raw_user_meta_data` keys the backend reads as a
 * fallback (`x_conversions_service._record_signup_sync`).
 */
export function landingAttributionSignupData(): Record<string, string> {
  const a = readLandingAttribution();
  const out: Record<string, string> = {};
  if (a.twclid) out.x_twclid = a.twclid;
  if (a.utm_source) out.signup_utm_source = a.utm_source;
  if (a.utm_campaign) out.signup_utm_campaign = a.utm_campaign;
  return out;
}
