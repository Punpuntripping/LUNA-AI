/**
 * `window.Moyasar` — the global the CDN bundle installs.
 *
 * Declared rather than `any`-cast so every `init()` call site is checked against
 * the option shape in `lib/moyasar.ts`. A typo in `publishable_api_key` or an
 * amount handed over in SAR instead of halalas is a real, expensive class of bug
 * (plan trap 2); the compiler is the cheapest place to catch the first kind.
 *
 * Optional because the bundle is loaded on demand by `loadMoyasarForm()` — it
 * genuinely is absent on every page that is not /pay.
 */
import type { MoyasarGlobal } from "@/lib/moyasar";

declare global {
  interface Window {
    Moyasar?: MoyasarGlobal;
    /**
     * Safari-only Apple global. Its PRESENCE is the capability gate for
     * offering Apple Pay — moyasar.js 1.19.0 kills the whole form (verified
     * 2026-08-04) if 'applepay' is in `methods` on a browser without it.
     * Minimal surface: only what the gate calls.
     */
    ApplePaySession?: {
      canMakePayments(): boolean;
    };
  }
}

export {};
