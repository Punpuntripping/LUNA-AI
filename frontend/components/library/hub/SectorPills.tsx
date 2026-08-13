import Link from "next/link";
import { cn } from "@/lib/utils";
import { sectorPath } from "@/lib/library/sectors";

/**
 * The القطاع chips at the foot of a hub card — the second axis into the corpus
 * (D11). Rendered through `CardShell`'s `footer` slot, i.e. OUTSIDE the card's
 * own anchor, which is what makes them legal links rather than the dead
 * `<span>`s they used to be.
 *
 * ⚠ THE SLUG COMES FROM THE API, NEVER FROM THE NAME. `slugs` is the
 * `name_ar → slug` map a SERVER component resolved via `getSectorSlugMap()`.
 * A name with no entry renders as PLAIN TEXT — never a guessed href, never a
 * broken link. That covers three real cases: the map failed to load, the
 * backend has not shipped `/sectors` yet, and a corpus value that simply is not
 * one of the 38 (e.g. a judgment `legal_domains[]` entry outside the
 * vocabulary). All three degrade to exactly what shipped before D11.
 *
 * `slugs` is optional so a CLIENT-rendered card (the authed reveal inside
 * `HubCtaWall`, the مكتبتي shelf) can pass nothing and still render.
 *
 * No `"use client"` — this renders on the server for every hub grid and rides
 * into the client graph only where its parent card already does.
 */
const PILL_CLASS =
  "inline-flex items-center rounded-full bg-pill px-2 py-0.5 text-xs font-medium text-pill-fg";

export function SectorPills({
  names,
  slugs,
  max = 3,
  className,
}: {
  /** Arabic sector names off the item payload (`sectors[]` / `domains[]`). */
  names: string[] | undefined;
  /** `name_ar → slug`, from `getSectorSlugMap()`. Omit ⇒ plain-text pills. */
  slugs?: Record<string, string>;
  max?: number;
  className?: string;
}) {
  if (!names || names.length === 0) return null;

  return (
    <ul className={cn("flex flex-wrap gap-1.5 pt-3", className)}>
      {names.slice(0, max).map((name) => {
        const slug = slugs?.[name];
        return (
          <li key={name}>
            {slug ? (
              <Link
                href={sectorPath(slug)}
                className={cn(
                  PILL_CLASS,
                  "transition-colors hover:bg-accent-soft hover:text-accent-brand",
                )}
              >
                {name}
              </Link>
            ) : (
              <span className={PILL_CLASS}>{name}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
