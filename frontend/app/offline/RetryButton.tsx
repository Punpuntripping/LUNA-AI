"use client";

/**
 * «إعادة المحاولة» on the offline screen. The service worker serves /offline
 * AT THE URL the user was navigating to, so a plain reload retries that page —
 * not /offline itself.
 */
export function RetryButton() {
  return (
    <button
      type="button"
      onClick={() => window.location.reload()}
      className="inline-flex h-11 items-center justify-center rounded-lg bg-primary px-6 text-sm font-medium text-primary-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      style={{
        // Inline fallbacks for the case where the hashed CSS isn't in the
        // browser cache — see page.tsx.
        backgroundColor: "var(--primary, #4A6B5F)",
        color: "var(--primary-fg, #FCF8F2)",
        border: "none",
        borderRadius: "0.5rem",
        padding: "0 1.5rem",
        height: "2.75rem",
        cursor: "pointer",
        fontFamily: "inherit",
      }}
    >
      إعادة المحاولة
    </button>
  );
}
