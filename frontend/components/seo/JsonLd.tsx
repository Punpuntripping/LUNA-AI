import type { JsonLdObject } from "@/lib/seo/schema";

interface JsonLdProps {
  /** One schema.org object, or an array of them, to embed on the page. */
  data: JsonLdObject | JsonLdObject[];
}

/**
 * Renders a `<script type="application/ld+json">` tag with structured data.
 *
 * Server component — no client JS, no hydration. The single escape of `<` to
 * its unicode form prevents a `</script>` sequence inside the JSON from
 * breaking out of the tag (the standard, XSS-safe serialization used across the
 * Next.js docs). All other characters are already JSON-safe.
 */
export function JsonLd({ data }: JsonLdProps) {
  const json = JSON.stringify(data).replace(/</g, "\\u003c");

  return (
    <script
      type="application/ld+json"
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: json }}
    />
  );
}
