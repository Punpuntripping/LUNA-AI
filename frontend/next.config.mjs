/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV !== "production";

const nextConfig = {
  // Standalone output for Railway deployment
  output: "standalone",

  // The /og ImageResponse route reads its fonts and logo from `assets/` at
  // runtime via `fs`. Standalone builds only copy files the tracer can see, and
  // a dynamic `fs.readFile(join(process.cwd(), ...))` isn't statically
  // analyzable — so include them explicitly for the /og route, along with
  // HarfBuzz's wasm, which harfbuzzjs loads relative to its own module.
  outputFileTracingIncludes: {
    "/og": [
      "./assets/fonts/**",
      "./assets/brand/**",
      "./node_modules/harfbuzzjs/dist/harfbuzz.wasm",
    ],
  },

  // harfbuzzjs is ESM with a top-level await that fetches its wasm from
  // `new URL("harfbuzz.wasm", import.meta.url)`. Left to Node, that resolves
  // to the file in node_modules; bundled, it would point into .next/server.
  serverExternalPackages: ["harfbuzzjs"],

  // Enable React strict mode
  reactStrictMode: true,

  // Security headers
  async headers() {
    // Cloudflare serves JS Detections / the challenge platform from
    // same-origin `/cdn-cgi/...`. Named explicitly (a bare "/cdn-cgi/" is not
    // a valid CSP source — the grammar requires a host) so the allowance
    // survives any future tightening of 'self', e.g. a move to nonces. This
    // must be deployed BEFORE JS Detections is enabled at the edge, or the
    // script is blocked silently.
    const cdnCgi = "https://rayhanai.com/cdn-cgi/";
    // Cloudflare Turnstile on the anonymous «اسأل ريحان» ask. It needs BOTH
    // script-src (api.js) and frame-src (the challenge renders in an iframe) —
    // miss either and the widget silently produces no token, which 403s every
    // anon ask once TURNSTILE_SECRET_KEY is set on the backend.
    const turnstile = "https://challenges.cloudflare.com";
    // Moyasar embedded payment form (`.claude/plans/moyasar_payments.md` Phase D).
    // The form is a CDN script + stylesheet, and the card fields post straight
    // to api.moyasar.com — so it needs script-src AND style-src for the bundle,
    // plus connect-src for the API. A missing host here is a SILENTLY BLANK
    // form (trap 7), and the CSP is baked at build time, so this must ship
    // before or with /pay. `frame-src` needs nothing: 3DS is a full-page
    // redirect, not an embedded challenge — still true on 2.x, whose bundle
    // contains zero iframes despite what Moyasar support said.
    //
    // ⚠ CSP IS BAKED AT BUILD TIME. These are `next.config.mjs` literals, not
    // env vars, so changing them needs a frontend REBUILD + REDEPLOY — an
    // env flip on Railway does nothing. Ship this with the form migration or
    // the checkout goes blank for everyone.
    //
    // The bundle now comes from jsDelivr (`moyasar-payment-form@2.2.13` npm —
    // `cdn.moyasar.com/mpf/` is a deprecated track that cannot tokenize Apple
    // Pay; see `frontend/lib/moyasar.ts`), and 2.x injects Apple's OWN SDK
    // from applepay.cdn-apple.com whenever an `apple_pay` config is present —
    // that host is a hard requirement of Apple Pay working at all, not a
    // nice-to-have. `cdn.moyasar.com` stays allowlisted deliberately: nothing
    // loads from it after the migration, but dropping it in the same change
    // would fuse the migration to a tightening that cannot be rolled back
    // independently of it.
    //
    // jsDelivr has a second consumer already: `lib/pdf-thumbnail.ts` pulls
    // pdfjs-dist's worker from it, and worker-src falls back through
    // child-src to script-src — so that worker was blocked until this line
    // existed.
    const moyasarCdn = "https://cdn.moyasar.com";
    const moyasarApi = "https://api.moyasar.com";
    const jsdelivrCdn = "https://cdn.jsdelivr.net";
    const applePaySdk = "https://applepay.cdn-apple.com";
    // Deliberately NOT allowlisted: the 2.x bundle ships an AppSignal client
    // that POSTs to appsignal-endpoint.net/collect. It is blocked by omission
    // from connect-src and stays that way — shipping our checkout's error
    // telemetry to an unnamed third party is a data-protection decision nobody
    // made, and the form works without it.
    const scriptSrc = isDev
      ? `'self' 'unsafe-inline' 'unsafe-eval' ${cdnCgi} ${turnstile} ${moyasarCdn} ${jsdelivrCdn} ${applePaySdk}`
      : `'self' 'unsafe-inline' ${cdnCgi} ${turnstile} ${moyasarCdn} ${jsdelivrCdn} ${applePaySdk}`;
    const frameSrc = `https://www.youtube-nocookie.com ${turnstile}`;

    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `default-src 'self'; script-src ${scriptSrc}; style-src 'self' 'unsafe-inline' ${moyasarCdn} ${jsdelivrCdn}; img-src 'self' https://*.supabase.co https://img.youtube.com data:; connect-src 'self' ${isDev ? "http://localhost:8000 " : ""}https://api.rayhanai.com https://*.supabase.co https://*.railway.app wss://*.supabase.co ${moyasarApi}; font-src 'self' https://fonts.gstatic.com; frame-src ${frameSrc}`,
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
        ],
      },
      // The service worker (`public/sw.js`, pwa_step1.md §1C). Never cached,
      // so a fix — or the kill-switch worker — reaches phones on the next
      // navigation instead of after an HTTP-cache TTL. This covers the ORIGIN
      // only: Cloudflare needs its own bypass cache rule for /sw.js, or the
      // edge pins a broken worker regardless of this header.
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ];
  },

  // Canonical host: redirect www → apex. The `host` condition only matches
  // when the request actually arrives on www.rayhanai.com, so this is inert
  // on localhost and on the *.railway.app domain — safe to ship anytime.
  async redirects() {
    return [
      {
        source: "/:path*",
        has: [{ type: "host", value: "www.rayhanai.com" }],
        destination: "https://rayhanai.com/:path*",
        permanent: true,
      },
      // The PWA install guide moved under «عن ريحان» (2026-09-24). `/app` was
      // live, in the sitemap and shared for a few hours — keep it resolving.
      {
        source: "/app",
        destination: "/about_us/app",
        permanent: true,
      },
    ];
  },

  // API proxy for development (avoid CORS issues)
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },

  // Bake imported markdown (.md) files into the bundle as raw strings. Used by
  // the public legal pages (/terms, /privacy) which `import md from "...md"`.
  // `asset/source` resolves the import to the file's raw text contents, so the
  // content travels inside the standalone build with zero runtime fs reads.
  webpack(config) {
    config.module.rules.push({ test: /\.md$/, type: "asset/source" });
    return config;
  },

  // Image optimization for document previews
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "*.supabase.co",
        pathname: "/storage/v1/object/**",
      },
    ],
  },
};

export default nextConfig;
