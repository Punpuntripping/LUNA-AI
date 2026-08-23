import Link from "next/link";
import { CardShell } from "@/components/library/hub/CardShell";
import { type ComplianceHubItem } from "@/lib/library/api";
import { entityPath, entitySlugForName } from "@/lib/library/entities";
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

const ENTITY_CHIP_CLASS =
  "inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-xs font-medium text-pill-fg";

/**
 * One card in the /compliance guide grid: the guide's title, one line of our own
 * orientation text, how many screenshots it walks through, and — at the foot —
 * the issuing entity, linked to that entity's section.
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
 *
 * ── WHY THE ENTITY CHIP MOVED TO THE FOOT (2026-08-23) ──────────────────────
 * It used to be a dead `<span>` above the title. The card is where a reader
 * LEARNS an entity's name, so it is where the «الجهة» axis should be
 * discoverable without opening the switcher — and a link cannot live in the card
 * BODY, because `CardShell` wraps the body in the card's own anchor and nesting
 * `<a>` inside `<a>` is invalid HTML the parser silently un-nests. `footer` is
 * the slot that exists for exactly this, a sibling of the anchor rather than a
 * child, and it is the same place `JudgmentCard` puts its `SectorPills`. Its
 * position under the summary also keeps it visually SUBORDINATE to the title:
 * one card, one headline, and a quiet second exit at the bottom.
 *
 * ⚠ AN UNKNOWN PROVIDER RENDERS AS PLAIN TEXT, NEVER A GUESSED HREF. The slug
 * comes from the closed 28-value mirror; a corpus re-ingest that introduces a
 * new `provider_name` degrades to exactly the chip that shipped before, not to a
 * 404. Same rule `SectorPills` applies to a sector name with no slug.
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
  const provider = item.provider_name?.trim() ?? "";
  const entitySlug = provider ? entitySlugForName(provider) : null;

  return (
    <CardShell
      href={target}
      footer={
        provider ? (
          <div className="flex flex-wrap gap-1.5 pt-3">
            {entitySlug ? (
              <Link
                href={entityPath(entitySlug)}
                className={`${ENTITY_CHIP_CLASS} transition-colors hover:bg-accent-soft hover:text-accent-brand`}
              >
                {provider}
              </Link>
            ) : (
              <span className={ENTITY_CHIP_CLASS}>{provider}</span>
            )}
          </div>
        ) : undefined
      }
    >
      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground transition-colors group-hover:text-primary">
        {guideDisplayTitle(item.title, imageCount)}
      </h2>

      {item.summary && (
        <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-text-secondary">
          {item.summary}
        </p>
      )}

      {/* Hidden on the text-only guides rather than shown as a zero — and on
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
