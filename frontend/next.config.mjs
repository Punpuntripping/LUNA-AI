/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV !== "production";

const nextConfig = {
  // Standalone output for Railway deployment
  output: "standalone",

  // Enable React strict mode
  reactStrictMode: true,

  // Security headers
  async headers() {
    const scriptSrc = isDev
      ? "'self' 'unsafe-inline' 'unsafe-eval'"
      : "'self' 'unsafe-inline'";

    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `default-src 'self'; script-src ${scriptSrc}; style-src 'self' 'unsafe-inline'; img-src 'self' https://*.supabase.co data:; connect-src 'self' ${isDev ? "http://localhost:8000 " : ""}https://api.rayhanai.com https://*.supabase.co https://*.railway.app wss://*.supabase.co; font-src 'self' https://fonts.gstatic.com`,
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
