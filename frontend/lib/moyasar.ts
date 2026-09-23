/**
 * Moyasar embedded payment form — asset pinning, types, and the one-shot loader.
 * (`.claude/plans/moyasar_payments.md` Phase D;
 * `.claude/plans/applepay_auto_renewal_fix.md` Phase 2 for the 2.x migration.)
 *
 * ⚠ WE MOVED CHANNELS ON 2026-09-23: `cdn.moyasar.com/mpf/` → the npm package
 * `moyasar-payment-form` on jsDelivr. This was NOT a version bump for its own
 * sake. `mpf/` is a frozen track — Moyasar's docs no longer reference it,
 * support confirmed it is deprecated and receives no feature updates, our
 * 1.19.0 was last touched 2025-07-26, and every other `mpf/` path (2.2.13
 * included) 403s. Concretely it cost us money: 1.x's Apple Pay source builder
 * emits `{type:"applepay", token}` with NO `save_card`, so every Apple Pay
 * buyer on a renewing plan completed a purchase, got no stored token, and was
 * silently never enrolled in auto-renewal. 2.x emits
 * `{type:"applepay", token, manual, save_card}` — that key is the whole fix.
 *
 * ⚠ THE VERSION STAYS PINNED EXACTLY, and on this channel that matters MORE,
 * not less: unlike `/mpf/`, jsDelivr happily resolves `@latest`, a bare
 * `/npm/moyasar-payment-form/`, and range specs like `@2`. Any of those would
 * silently swap the checkout bundle under us on Moyasar's release schedule, and
 * the form's failure mode is a blank div, not an exception. Bumping is a manual
 * edit of the constant below — which is why it lives here and never inline in
 * JSX. Moyasar publishes no changelog, so a bump means diffing bundles.
 *
 * ⚠ NO SRI on these URLs. jsDelivr GENERATES `*.umd.min.js` on demand (it
 * answers with "Skipped minification because the original file appears to be
 * already minified" and names `moyasar.umd.js` as the source), and its own
 * banner warns that dynamically generated files have no stable hash. An
 * `integrity` attribute here would be a checkout that dies on a CDN-side
 * re-generation.
 *
 * The assets are loaded ONLY on /pay — never from the root layout. Two reasons:
 * a ~245 KB script plus its stylesheet on every page is dead weight for the 99%
 * of navigations that are not a checkout, and the surface reachable by a CDN
 * script should be as small as the feature that needs it.
 *
 * The CSP hosts this needs (`cdn.jsdelivr.net` on script-src + style-src,
 * `applepay.cdn-apple.com` on script-src because 2.x injects Apple's own SDK
 * itself whenever an `apple_pay` config is present, `api.moyasar.com` on
 * connect-src) live in `next.config.mjs`. A missing host is a silently blank
 * form, not an error — if the form never appears, check the CSP report before
 * anything else. `frame-src` needs nothing: 3DS in 2.2.13 is still a full-page
 * `window.location.href = transaction_url` redirect. Moyasar support claimed
 * 2.x wraps 3DS in an iframe overlay; the shipped bundle contains zero iframes
 * (verified 2026-09-22). Do not design around that claim.
 */

export const MOYASAR_FORM_VERSION = "2.2.13";

export const MOYASAR_SCRIPT_URL = `https://cdn.jsdelivr.net/npm/moyasar-payment-form@${MOYASAR_FORM_VERSION}/dist/moyasar.umd.min.js`;
export const MOYASAR_STYLE_URL = `https://cdn.jsdelivr.net/npm/moyasar-payment-form@${MOYASAR_FORM_VERSION}/dist/moyasar.css`;

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
   * Where the SDK performs Apple's merchant validation. **Moyasar's own
   * endpoint, called straight from the browser** — always
   * `MOYASAR_APPLEPAY_VALIDATE_URL`; see that constant for the full story.
   *
   * ⚠ This docstring used to claim the opposite ("our backend route that
   * proxies…"). That belief is what shipped a proxy route here until
   * 2026-08-18 and silently killed every Apple Pay payment in production:
   * Web Merchant Registration means Moyasar holds the merchant identity, so
   * the server-side half of validation is THEIRS, not ours, and putting our
   * origin between their SDK and their API broke both the preflight and the
   * auth. Never point this at our own host.
   */
  validate_merchant_url: string;
  /**
   * Tokenize the wallet credential after a successful charge — the Apple Pay
   * twin of `credit_card.save_card`, and the only way an Apple Pay buyer ever
   * auto-renews.
   *
   * Reaches the source verbatim: 2.2.13 builds
   * `{type:"applepay", token, manual: !!apple_pay.manual, save_card: !!apple_pay.save_card}`.
   * On 1.19.0 this key did not exist in the bundle at all, which is why every
   * Apple Pay purchase on a renewing plan came back with `source.token: null`
   * and dropped out of the renewal sweep as `skipped_no_method` — at INFO,
   * indistinguishable from a `basic` purchase. Nothing is recoverable
   * retroactively; an affected buyer can only be re-enrolled by buying again.
   *
   * Pass it ONLY when the server says the plan renews (`requiresConsent`), the
   * same PDPL data-minimisation rule the card path follows: no credential is
   * stored for a purchase that will never be charged a second time.
   */
  save_card?: boolean;
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
   *
   * The 2.2.13 migration did NOT re-verify that 2.x still clobbers the id, and
   * deliberately so: the node reference cannot break either way, so there is
   * nothing to gain from finding out.
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
  /**
   * ⚠ MUST BE DECLARED `async`, and the return type says so — 2.x validates
   * `constructor.name === "AsyncFunction" || String(fn).startsWith("async")`
   * on BOTH callbacks and, when it fails, throws away the form and renders its
   * own «Form configuration issue!» panel instead. A plain arrow here is a
   * total checkout outage on every device, which is exactly what shipped on
   * 2026-09-23 — 1.19.0 never checked, so the 2.x migration surfaced it.
   * `Promise<unknown>` alone (no bare `void`) is deliberate: it makes a
   * non-async handler a type error rather than a production incident.
   */
  on_failure?: (error: unknown) => Promise<unknown>;
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
