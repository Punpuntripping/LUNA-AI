/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV !== "production";

const nextConfig = {
  // Standalone output for Railway deployment
  output: "standalone",

  // The /og ImageResponse route reads its Arabic TTFs from
  // `assets/fonts/` at runtime via `fs`. Standalone builds only copy files the
  // tracer can see, and a dynamic `fs.readFile(join(process.cwd(), ...))` isn't
  // statically analyzable — so include the fonts explicitly for the /og route.
  outputFileTracingIncludes: {
    "/og": ["./assets/fonts/**"],
  },

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
    const scriptSrc = isDev
      ? `'self' 'unsafe-inline' 'unsafe-eval' ${cdnCgi} ${turnstile}`
      : `'self' 'unsafe-inline' ${cdnCgi} ${turnstile}`;
    const frameSrc = `https://www.youtube-nocookie.com ${turnstile}`;

    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `default-src 'self'; script-src ${scriptSrc}; style-src 'self' 'unsafe-inline'; img-src 'self' https://*.supabase.co https://img.youtube.com data:; connect-src 'self' ${isDev ? "http://localhost:8000 " : ""}https://api.rayhanai.com https://*.supabase.co https://*.railway.app wss://*.supabase.co; font-src 'self' https://fonts.gstatic.com; frame-src ${frameSrc}`,
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
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
