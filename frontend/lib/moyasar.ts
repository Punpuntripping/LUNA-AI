/**
 * Moyasar embedded payment form — asset pinning, types, and the one-shot loader.
 * (`.claude/plans/moyasar_payments.md` Phase D.)
 *
 * ⚠ THE VERSION IS PINNED IN THE CDN PATH AND THERE IS NO `latest` ALIAS
 * (`/mpf/latest/` → 403). Bumping it is a manual edit of the constant below,
 * which is why it lives here and never inline in JSX: a stale form version rots
 * silently, and is the likely cause of a future "a payment method stopped
 * appearing" report (plan trap 10). Available at the time of writing: 1.13.0,
 * 1.14.0, 1.15.0, 1.16.0, 1.18.0, 1.19.0 (no 1.17.0).
 *
 * The assets are loaded ONLY on /pay — never from the root layout. Two reasons:
 * a 98 KB script + 70 KB stylesheet on every page is dead weight for the 99% of
 * navigations that are not a checkout, and the surface reachable by a CDN script
 * should be as small as the feature that needs it.
 *
 * The three CSP hosts this needs (`cdn.moyasar.com` on script-src + style-src,
 * `api.moyasar.com` on connect-src) live in `next.config.mjs`. A missing host is
 * a silently blank form, not an error — if the form never appears, check the CSP
 * report before anything else.
 */

export const MOYASAR_FORM_VERSION = "1.19.0";

export const MOYASAR_SCRIPT_URL = `https://cdn.moyasar.com/mpf/${MOYASAR_FORM_VERSION}/moyasar.js`;
export const MOYASAR_STYLE_URL = `https://cdn.moyasar.com/mpf/${MOYASAR_FORM_VERSION}/moyasar.css`;

/**
 * Apple Pay merchant validation — **Moyasar's endpoint, not ours.**
 *
 * This is the value Moyasar's own guide prescribes verbatim
 * (docs.moyasar.com/guides/apple-pay/apple-pay-web): the browser calls
 * `/v1/applepay/initiate` DIRECTLY. It needs no Apple Developer account and no
 * merchant-hosted route — Web Merchant Registration means Moyasar holds the
 * merchant identity, and the publishable key in the body is the whole auth.
 *
 * ⚠ Do NOT point this back at our backend. We shipped a proxy route here until
 * 2026-08-18 (`moyasar_payments.md` guessed one would be needed — "likely a
 * thin backend route", never verified in sandbox) and it silently killed every
 * Apple Pay payment in production: moyasar.js sends its own
 * `X-Moyasar-Form-Version` header on this fetch, which failed our CORS
 * preflight with a 400, and it sends no Authorization header, which our authed
 * route would have 401'd. Moyasar's endpoint answers preflights with
 * `access-control-allow-origin: *` and explicitly allowlists that header —
 * verified live 2026-08-18. Their SDK and their API are built as a matched
 * pair; putting our origin between them is what broke it.
 *
 * `domain_name` is filled in by the SDK as `window.location.hostname` and must
 * match a domain registered under Moyasar → Apple Pay Domains (`rayhanai.com`;
 * the association file is served extensionless from `public/.well-known/`).
 * www 308-redirects to the apex, so checkout always runs on the registered
 * host — do not add a www checkout path without registering that domain too.
 */
export const MOYASAR_APPLEPAY_VALIDATE_URL =
  "https://api.moyasar.com/v1/applepay/initiate";

/** DOM id the form mounts into. Also the `element` selector handed to `init`. */
export const MOYASAR_FORM_ELEMENT_ID = "moyasar-payment-form";

/**
 * A payment object as the form hands it back. Only the fields we actually read
 * are typed — the payload is much larger, and modelling all of it would create
 * a second, drifting copy of a contract the backend already re-fetches and
 * verifies. `id` is the only field we forward, and even that is re-fetched
 * server-side with our secret key before anything is granted (plan §3).
 */
export interface MoyasarPayment {
  id: string;
  status: string;
  amount: number;
  currency: string;
  description?: string;
  source?: { type?: string; message?: string | null } | null;
}

/** Payment methods the form may render. STC Pay excluded by decision 2026-08-03. */
export type MoyasarMethod = "creditcard" | "applepay" | "stcpay";

/** Card networks offered inside the card method. */
export type MoyasarNetwork = "mada" | "visa" | "mastercard" | "amex";

export interface MoyasarApplePayConfig {
  country: string;
  /**
   * Merchant name on the Apple Pay sheet. ⚠ ASCII ONLY: moyasar.js forwards
   * this as `display_name` to `/v1/applepay/initiate`, which rejects anything
   * else — "Invalid display name, only ASCII is supported." (their live API,
   * verified 2026-08-18). An Arabic label here silently kills the payment
   * sheet within a second of opening.
   */
  label: string;
  /**
   * Our backend route that proxies Moyasar's `GET /v1/applepay/initiate`. Apple
   * requires merchant validation to come from a server, so this cannot be a
   * client-side call.
   */
  validate_merchant_url: string;
}

export interface MoyasarInitOptions {
  /**
   * Mount point — pass the **DOM node**, not an id selector.
   *
   * ⚠ Verified on prod 2026-08-04: moyasar.js 1.19.0 OVERWRITES the
   * container's `id` with its own (`mysr-form-form-el`) during mount, and its
   * config object re-runs `querySelector` on the stored selector string on
   * every internal access (e.g. RTL detection inside the amount label's
   * render). An id selector therefore stops matching mid-mount and the form
   * kills itself with "Element: null is not a valid element". Their docs'
   * `.mysr-form` class selector survives the rewrite — but the node reference
   * is immune by construction, so that is what we pass.
   */
  element: string | HTMLElement;
  /** ⚠ HALALAS, not SAR — a missed ×100 charges 0.49 SAR (plan trap 2). */
  amount: number;
  currency: string;
  description: string;
  publishable_api_key: string;
  callback_url: string;
  /** Free-form string map; carries our `payment_id` so the webhook can bind. */
  metadata?: Record<string, string>;
  methods?: MoyasarMethod[];
  supported_networks?: MoyasarNetwork[];
  language?: "ar" | "en";
  apple_pay?: MoyasarApplePayConfig;
  credit_card?: { save_card?: boolean };
  /**
   * Fires after the payment object exists but BEFORE any 3DS redirect. This is
   * the only chance to persist the Moyasar id, because 3DS destroys the page
   * (plan trap 9). May return a promise — the form awaits it.
   */
  on_completed?: (payment: MoyasarPayment) => void | Promise<unknown>;
  on_failure?: (error: unknown) => void;
}

export interface MoyasarGlobal {
  init: (options: MoyasarInitOptions) => void;
}

/**
 * Injects the script + stylesheet once per document and resolves with the
 * global. Concurrent callers share the same promise; a completed load resolves
 * immediately on every later call.
 *
 * On failure the memoized promise is cleared so a retry actually retries rather
 * than replaying a cached rejection — a checkout page that fails to load the
 * form must be recoverable with the «إعادة المحاولة» button.
 */
let loadPromise: Promise<MoyasarGlobal> | null = null;

export function loadMoyasarForm(): Promise<MoyasarGlobal> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("moyasar: browser-only"));
  }
  if (window.Moyasar) return Promise.resolve(window.Moyasar);
  if (loadPromise) return loadPromise;

  loadPromise = new Promise<MoyasarGlobal>((resolve, reject) => {
    // Stylesheet first, and deliberately NOT awaited: the form is usable the
    // moment the script runs, and blocking on CSS would turn a slow stylesheet
    // into a checkout that never appears. An unstyled flash is the better
    // failure mode than no form.
    if (!document.querySelector(`link[href="${MOYASAR_STYLE_URL}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = MOYASAR_STYLE_URL;
      document.head.appendChild(link);
    }

    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${MOYASAR_SCRIPT_URL}"]`,
    );

    const settle = () => {
      if (window.Moyasar) resolve(window.Moyasar);
      else reject(new Error("moyasar: script loaded but global is missing"));
    };

    if (existing) {
      existing.addEventListener("load", settle, { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("moyasar: script failed to load")),
        { once: true },
      );
      // Already finished before this caller arrived.
      if (window.Moyasar) resolve(window.Moyasar);
      return;
    }

    const script = document.createElement("script");
    script.src = MOYASAR_SCRIPT_URL;
    script.async = true;
    script.addEventListener("load", settle, { once: true });
    script.addEventListener(
      "error",
      () => reject(new Error("moyasar: script failed to load")),
      { once: true },
    );
    document.head.appendChild(script);
  }).catch((err: unknown) => {
    loadPromise = null;
    throw err;
  });

  return loadPromise;
}
