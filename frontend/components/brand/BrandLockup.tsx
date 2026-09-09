import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * The «ريحان» lockup — Kufic wordmark + monstera leaf — theme-swapped.
 *
 * ONE component for every chrome surface (site header, sidebar, login, the
 * empty-chat hero) because the swap is two `<Image>`s and a `dark:` pair, and
 * four hand-rolled copies of that is four chances to ship a logo that is
 * invisible in one theme. Masters live in `public/brand/`; these two files are
 * the trimmed, downscaled render targets.
 *
 * ⚠ The two files are NOT one artwork recoloured, and neither can stand in for
 * the other:
 *   - `lockup-on-light.png` — deep green `#264236`, OUTLINE leaf. Cut from the
 *     cream-ground master the ground was baked into, so it drops onto any light
 *     surface. Shown in light mode.
 *   - `lockup-on-dark.png` — near-white wordmark whose charcoal `#18141A` leaf
 *     body merges into the dark canvas (`--canvas: #1A1917`) and leaves its
 *     sage veins drawing the leaf. Shown in dark mode. It reads as line art,
 *     which is what makes the pair look intentional rather than mismatched.
 *
 * Sized by HEIGHT with `w-auto`. The two lockups have different aspect ratios
 * (1.472 vs 1.387 — the outline leaf sits tighter), so pinning a width would
 * letterbox one of them. Pass the height through `className`.
 *
 * The lockup CONTAINS the wordmark, so never pair it with a «ريحان» text label:
 * that renders the brand name twice. `alt` carries the name for screen readers
 * and for the case where neither image loads.
 *
 * Server component — `dark:` is a CSS variant, so no client JS is involved in
 * the swap (next-themes writes `class` on <html>; see `providers.tsx`).
 */
export function BrandLockup({
  className,
  priority = false,
}: {
  /** Height utility, e.g. `h-10`. Width follows from the aspect ratio. */
  className?: string;
  /** Set on above-the-fold surfaces (the site header) to skip lazy loading. */
  priority?: boolean;
}) {
  return (
    <>
      <Image
        src="/brand/lockup-on-light.png"
        alt="ريحان"
        width={480}
        height={326}
        priority={priority}
        className={cn("w-auto dark:hidden", className)}
      />
      <Image
        src="/brand/lockup-on-dark.png"
        alt="ريحان"
        width={480}
        height={346}
        priority={priority}
        className={cn("hidden w-auto dark:block", className)}
      />
    </>
  );
}
