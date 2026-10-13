import { textSize, typeTiers } from "./tokens.generated";

/** Type variants: the web's phone reading tiers + heading sizes.
 *  Headings get 1.5× leading — Naskh's tall ascenders/descenders clip below that. */
export const typeVariants = {
  xs: typeTiers.xs,
  sm: typeTiers.sm,
  base: typeTiers.base,
  lg: typeTiers.lg,
  xl: typeTiers.xl,
  read: typeTiers.read,
  h3: { fontSize: textSize.h3, lineHeight: Math.round(textSize.h3 * 1.5) },
  h2: { fontSize: textSize.h2, lineHeight: Math.round(textSize.h2 * 1.5) },
  h1: { fontSize: textSize.h1, lineHeight: Math.round(textSize.h1 * 1.5) },
} as const;

export type TypeVariant = keyof typeof typeVariants;

/** Cap OS font scaling on chrome so large-text settings don't break layouts. */
export const MAX_FONT_SCALE = 1.3;
