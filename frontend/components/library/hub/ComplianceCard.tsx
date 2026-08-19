import { CardShell } from "@/components/library/hub/CardShell";
import { type ComplianceHubItem } from "@/lib/library/api";
import { guideDisplayTitle } from "@/lib/library/guide";

/**
 * «كم خطوة مصوّرة؟» in grammatical Arabic. The counted noun changes shape with
 * the number — 3–10 take the plural (خطوات), 11+ go back to the singular
 * (خطوة) — so a single hardcoded phrase would be wrong for most guides
 * (`image_count` runs 1–69). Digits stay Latin, app-wide convention
 * (`lib/format/numerals`).
 */
function stepsHint(count: number): string {
  if (count === 1) return "خطوة مصوّرة واحدة";
  if (count === 2) return "خطوتان مصوّرتان";
  if (count <= 10) return `${count} خطوات مصوّرة`;
  return `${count} خطوة مصوّرة`;
}

/**
 * One card in the /compliance guide grid: issuing entity, the guide's title, one
 * line of our own orientation text, and how many screenshots the guide walks
 * through.
 *
 * WHAT SITS BEHIND IT: our own authored rewrite of that entity's official PDF
 * user-guide, published in full and ungated. What does NOT sit behind it is the
 * entity's own procedure text — الشروط / المستندات المطلوبة / الخطوات copied out
 * of the `services` corpus is what got the 2026-08-03 wing deleted. `summary`
 * answers "is this the service I need?"; the guide answers "how do I do it?" in
 * our words; the entity's service page — the only outbound link — remains the
 * place the truth is edited.
 *
 * The title goes through `guideDisplayTitle` for the same reason the guide page
 * does: every corpus title starts «الدليل الشامل:», and a card that says
 * «الشامل» over a page that says «الشامل بالصور» reads as two different guides.
 */
export function ComplianceCard({
  item,
  href,
}: {
  item: ComplianceHubItem;
  href?: string | null;
}) {
  // `href === undefined` keeps the hub's own link; `null` renders unlinked.
  const target = href === undefined ? `/compliance/${item.slug}` : href;
  const imageCount = item.image_count ?? 0;
  return (
    <CardShell href={target}>
      {item.provider_name && (
        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-xs font-medium text-pill-fg">
            {item.provider_name}
          </span>
        </div>
      )}

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {guideDisplayTitle(item.title, imageCount)}
      </h2>

      {item.summary && (
        <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-text-secondary">
          {item.summary}
        </p>
      )}

      {/* Hidden on the 10 text-only guides rather than shown as a zero — and on
          any card baked by a backend older than the guides release, whose
          payload carries no `image_count` at all. */}
      {imageCount > 0 && (
        <p className="mt-2.5 text-xs text-muted-foreground">
          {stepsHint(imageCount)}
        </p>
      )}
    </CardShell>
  );
}
