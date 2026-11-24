import type { AngleGapKind, AngleKind, AngleSectionSide } from "@/lib/contracts";

/**
 * The angle map's tints — the theme's existing status families only, so light
 * and dark come for free: يقوّي = success, يطعن = error, يجب الرد = warning.
 * `line` is the map's line along an angle's row, `chip` its section numbers.
 */
export const ANGLE_KIND_STYLE: Record<
  AngleKind,
  { solid: string; soft: string; text: string; edge: string; ring: string; line: string; chip: string }
> = {
  strengthen: {
    solid: "bg-success-fg text-success",
    soft: "bg-success text-success-fg",
    text: "text-success-fg",
    edge: "border-s-success-fg",
    ring: "ring-success-fg/60",
    line: "bg-success-fg",
    chip: "border-success-fg/50 text-success-fg",
  },
  attack: {
    solid: "bg-error-fg text-error",
    soft: "bg-error text-error-fg",
    text: "text-error-fg",
    edge: "border-s-error-fg",
    ring: "ring-error-fg/60",
    line: "bg-error-fg",
    chip: "border-error-fg/50 text-error-fg",
  },
  answer: {
    solid: "bg-warning-fg text-warning",
    soft: "bg-warning text-warning-fg",
    text: "text-warning-fg",
    edge: "border-s-warning-fg",
    ring: "ring-warning-fg/60",
    line: "bg-warning-fg",
    chip: "border-warning-fg/50 text-warning-fg",
  },
};

/** الموكّل / الخصم / محايد — the same families as the section owner tags. */
export const ANGLE_SIDE_TAG: Record<AngleSectionSide, string> = {
  own: "bg-primary/10 text-primary",
  opp: "bg-error text-error-fg",
  neutral: "bg-muted text-text-muted",
};

export const ANGLE_GAP_STYLE: Record<AngleGapKind, string> = {
  contradiction: "text-error-fg",
  missing: "text-info-fg",
  unanswered: "text-warning-fg",
};
