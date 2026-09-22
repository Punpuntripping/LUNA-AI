// Sadu border motif + the six colourways for the National Day greeting card.
//
// THE MOTIF is one period of the woven band that runs along the card's top and
// bottom edges: camel, calf, palm frond, and the X medallion. It was traced off
// the reference artwork at a 7px cell and merged into 138 rects on a 104×35
// grid, so it recolours cleanly and costs ~1.5KB instead of a bitmap per
// colourway. The source band's own palette (a maroon at chroma 39, against the
// kit's accents at chroma 15) would have dominated every card it touched and
// matched neither the Rayhan greens nor the National Day green — so the shapes
// are kept and the colour is the card's.
//
// Flat [x, y, w, h, x, y, w, h, …] in grid cells, not px.
export const MOTIF_W = 104;
export const MOTIF_H = 35;

export const MOTIF: readonly number[] = [
  0, 8, 1, 2, 0, 27, 1, 2, 0, 16, 2, 2, 0, 19, 2, 1, 0, 18, 3, 1, 0, 14, 5, 2,
  0, 20, 5, 2, 12, 18, 3, 3, 12, 11, 21, 1, 12, 12, 22, 1, 12, 17, 23, 1, 12,
  16, 24, 1, 12, 15, 25, 1, 12, 14, 26, 1, 12, 13, 27, 1, 13, 21, 2, 11, 14, 32,
  1, 1, 14, 10, 19, 1, 15, 8, 18, 2, 16, 18, 1, 15, 18, 18, 11, 1, 18, 6, 13, 2,
  21, 4, 7, 2, 28, 19, 1, 14, 30, 18, 1, 15, 36, 12, 4, 1, 37, 11, 4, 1, 38, 10,
  4, 1, 39, 7, 3, 3, 39, 16, 4, 2, 39, 4, 5, 1, 39, 5, 7, 2, 41, 18, 2, 2, 42,
  20, 2, 1, 43, 21, 16, 1, 44, 22, 15, 1, 45, 23, 14, 1, 46, 20, 13, 1, 47, 18,
  10, 2, 47, 24, 12, 1, 48, 25, 2, 8, 48, 17, 7, 1, 50, 16, 4, 1, 56, 26, 2, 7,
  56, 25, 3, 1, 64, 16, 2, 1, 64, 20, 2, 1, 64, 24, 2, 1, 65, 15, 2, 1, 65, 19,
  2, 1, 65, 23, 2, 1, 65, 27, 2, 1, 65, 14, 3, 1, 66, 18, 2, 1, 66, 22, 2, 1,
  66, 26, 2, 1, 66, 13, 3, 1, 67, 12, 2, 1, 67, 17, 2, 1, 67, 21, 2, 1, 67, 25,
  2, 1, 68, 16, 1, 1, 68, 20, 1, 1, 68, 24, 1, 1, 68, 15, 2, 1, 68, 19, 2, 1,
  68, 23, 2, 1, 68, 11, 3, 1, 69, 5, 1, 6, 69, 27, 1, 6, 70, 16, 1, 1, 70, 20,
  1, 1, 70, 24, 1, 1, 70, 17, 2, 1, 70, 21, 2, 1, 70, 25, 2, 1, 70, 13, 3, 1,
  71, 12, 1, 1, 71, 18, 2, 1, 71, 22, 2, 1, 71, 26, 2, 1, 71, 14, 3, 1, 72, 15,
  2, 1, 72, 19, 2, 1, 72, 23, 2, 1, 72, 27, 2, 1, 73, 16, 2, 1, 73, 20, 2, 1,
  73, 24, 2, 1, 79, 5, 5, 2, 79, 30, 5, 3, 80, 12, 1, 1, 80, 13, 2, 1, 80, 24,
  2, 1, 80, 14, 3, 1, 80, 23, 3, 1, 81, 7, 5, 1, 82, 15, 2, 1, 82, 22, 2, 1, 82,
  8, 5, 2, 82, 28, 5, 2, 83, 16, 2, 1, 83, 21, 2, 1, 84, 17, 2, 1, 84, 19, 2, 2,
  84, 10, 5, 2, 84, 25, 5, 3, 85, 18, 1, 1, 85, 12, 5, 1, 87, 13, 5, 2, 87, 23,
  5, 2, 89, 5, 8, 2, 89, 15, 8, 2, 89, 20, 8, 3, 89, 30, 8, 3, 90, 17, 6, 1, 91,
  7, 4, 1, 92, 8, 2, 2, 92, 28, 2, 2, 94, 13, 5, 2, 94, 23, 5, 2, 96, 12, 5, 1,
  97, 10, 5, 2, 97, 25, 5, 3, 99, 8, 5, 2, 99, 28, 5, 2, 100, 18, 1, 1, 100, 17,
  2, 1, 100, 19, 2, 2, 100, 7, 4, 1, 101, 16, 2, 1, 101, 21, 2, 1, 102, 5, 2, 2,
  102, 15, 2, 1, 102, 22, 2, 1, 102, 30, 2, 3, 103, 14, 1, 1, 103, 23, 1, 1,
];

export interface Colourway {
  id: string;
  /** Arabic label for the swatch's accessible name. */
  label: string;
  mode: "light" | "dark";
  /** Card field. */
  ground: string;
  /** Name, greeting, and the recoloured subline. */
  ink: string;
  /** Border bands, ticks, and the motif. */
  frame: string;
  /**
   * The emblem tile's three dark values, in the card's hue: `[field,
   * checkerDark, checkerMid]`, replacing the identity's #002627 / #031B1F /
   * #01333A. The lightness ladder of the original is kept so the checkerboard
   * keeps its internal depth — only the hue moves.
   *
   * Dark cards get a deeper ladder than light ones. Carried over unchanged,
   * the mid value lands on #163329 against a #17332A ground — the same colour
   * — and a quarter of the tile would dissolve into the card. Pushed down, the
   * mid sits ΔL*7 clear of the ground and the tile keeps its edges.
   */
  emblem: readonly [string, string, string];
}

// Three hues × two modes. Sage and aubergine are the kit's own accents; navy is
// derived at hue 258° to sit between them at matched lightness and chroma, so
// the three read as one family rather than two plus a guest.
//
// Every pair below was contrast-checked: ink on ground ≥ 10:1, frame on ground
// ≥ 5:1. The light grounds are tints at L*93.5 with chroma pulled to a third —
// enough hue to tell three cards apart, light enough that the ink stays black-
// on-paper. The dark grounds sit at L*19 under the same cream ink, which is why
// their contrast figures are near-identical.
export const COLOURWAYS: readonly Colourway[] = [
  {
    id: "sage-light",
    label: "سالڤيا فاتح",
    mode: "light",
    ground: "#E3EFEA",
    ink: "#1B3D32",
    frame: "#4A6B5F",
    emblem: ["#09261D", "#001D14", "#163329"],
  },
  {
    id: "aubergine-light",
    label: "باذنجاني فاتح",
    mode: "light",
    ground: "#EFEBF4",
    ink: "#3D324A",
    frame: "#4C4158",
    emblem: ["#261D30", "#1C1426", "#332A3D"],
  },
  {
    id: "navy-light",
    label: "كحلي فاتح",
    mode: "light",
    ground: "#E5EDF6",
    ink: "#1E394E",
    frame: "#3F576C",
    emblem: ["#0A2334", "#001A2A", "#193041"],
  },
  {
    id: "sage-dark",
    label: "سالڤيا داكن",
    mode: "dark",
    ground: "#17332A",
    ink: "#F3EDE4",
    frame: "#90A59D",
    emblem: ["#001B11", "#001406", "#07241B"],
  },
  {
    id: "aubergine-dark",
    label: "باذنجاني داكن",
    mode: "dark",
    ground: "#342A3E",
    ink: "#F3EDE4",
    frame: "#A69DAE",
    emblem: ["#1A1224", "#14071D", "#241C2E"],
  },
  {
    id: "navy-dark",
    label: "كحلي داكن",
    mode: "dark",
    ground: "#1A3042",
    ink: "#F3EDE4",
    frame: "#94A2B1",
    emblem: ["#001827", "#001020", "#082232"],
  },
];

export const DEFAULT_COLOURWAY = COLOURWAYS[0];

export function colourwayById(id: string | null | undefined): Colourway {
  return COLOURWAYS.find((c) => c.id === id) ?? DEFAULT_COLOURWAY;
}
