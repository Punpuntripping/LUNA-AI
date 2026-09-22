// The National Day font library, as the card offers it.
//
// All eight families are the Saudi fonts published by وزارة الثقافة (the
// Ministry of Culture) at engage.moc.gov.sa/e/fonts/saudi-font — the labels
// below are the names it gives them. The Ministry remains the sole owner of
// the intellectual property; the page credits it, and that credit is not
// decoration — the licence requires copyright notices be retained.
//
// All eight families ship in the official drop. They are NOT interchangeable
// at a given px size: measured against the same Arabic string at 100px, the
// ink height runs from 56px (Arabic Poetry) to 121px (Handicrafts). Setting
// them all at one size would make choosing a font look like choosing a size,
// so every entry carries a `scale` that normalises it to Camel's ink height.
// The numbers below are measured, not guessed — see the table in the commit.
//
// WEIGHT. Only Camel, Handicrafts, Al-Awwal and Saudi ship more than one
// weight. The rest are single-weight by design; for those the name and the
// greeting are separated by size and opacity instead of by weight.
//
// LATIN. Handicrafts has no Latin glyphs at all — a Latin-spelled name would
// render as tofu. Every stack therefore falls back to Camel before the system
// serif, so a Latin name in Handicrafts lands on Camel rather than on Times.

export interface FontEntry {
  id: string;
  /** Shown on the picker chip. */
  label: string;
  /** CSS family name this face is registered under. */
  family: string;
  /** woff2 under /nd96/fonts/. `body` absent ⇒ single-weight family. */
  files: { display: string; body?: string };
  displayWeight: number;
  bodyWeight: number;
  /** Multiplier normalising this face's ink height to Camel's. */
  scale: number;
  /** Camel is declared in nd96.css and is present from first paint. */
  preloaded?: boolean;
}

export const FONTS: readonly FontEntry[] = [
  {
    id: "camel",
    label: "عام الإبل",
    family: "ND96 Camel",
    files: { display: "camel-display.woff2", body: "camel-body.woff2" },
    displayWeight: 800,
    bodyWeight: 500,
    scale: 1,
    preloaded: true,
  },
  {
    // The same family as the heading and the default, at Regular/Light instead
    // of ExtraBold/Medium — the plain cut, for a card that should read quietly
    // rather than shout. Its own entry and not a weight toggle, because the
    // picker's unit is "how the card looks", and weight changes that as much
    // as a different face does.
    id: "camel-plain",
    label: "عادي",
    family: "ND96 Camel Plain",
    files: {
      display: "camelplain-display.woff2",
      body: "camelplain-body.woff2",
    },
    displayWeight: 400,
    bodyWeight: 300,
    // 1.059, not 1: Regular's cap height is 658 against ExtraBold's 697, so
    // set at the same nominal it would sit ~6% shorter than every other face.
    scale: 1.059,
  },
  {
    id: "saudi",
    label: "السعودي",
    family: "ND96 Saudi",
    files: { display: "saudi-display.woff2", body: "saudi-body.woff2" },
    displayWeight: 700,
    bodyWeight: 400,
    scale: 1.072,
  },
  {
    id: "handicrafts",
    label: "عام الحرف اليدوية",
    family: "ND96 Handicrafts",
    files: {
      display: "handicrafts-display.woff2",
      body: "handicrafts-body.woff2",
    },
    displayWeight: 700,
    bodyWeight: 400,
    scale: 0.983,
  },
  {
    id: "masmak",
    label: "المصمك",
    family: "ND96 Masmak",
    files: { display: "masmak-display.woff2" },
    displayWeight: 700,
    bodyWeight: 700,
    scale: 1.053,
  },
  {
    id: "watad",
    label: "الوتد",
    family: "ND96 Watad",
    files: { display: "watad-display.woff2" },
    displayWeight: 400,
    bodyWeight: 400,
    scale: 1.017,
  },
  {
    id: "alawwal",
    label: "الأول",
    family: "ND96 Awwal",
    files: { display: "alawwal-display.woff2", body: "alawwal-body.woff2" },
    displayWeight: 700,
    bodyWeight: 400,
    scale: 1.214,
  },
  {
    id: "alnaseeb",
    label: "النسيب",
    family: "ND96 Naseeb",
    files: { display: "alnaseeb-display.woff2" },
    displayWeight: 400,
    bodyWeight: 400,
    scale: 1.566,
  },
  {
    id: "poetry",
    label: "عام الشعر العربي",
    family: "ND96 Poetry",
    files: { display: "poetry-display.woff2" },
    displayWeight: 500,
    bodyWeight: 500,
    scale: 2.125,
  },
];

export const DEFAULT_FONT = FONTS[0];

export function fontById(id: string | null | undefined): FontEntry {
  return FONTS.find((f) => f.id === id) ?? DEFAULT_FONT;
}

/** The stack a face is actually set in — Camel catches what it cannot draw. */
export function stackFor(font: FontEntry): string {
  return font.id === "camel"
    ? `"${font.family}", "Noto Naskh Arabic", serif`
    : `"${font.family}", "ND96 Camel", "Noto Naskh Arabic", serif`;
}

const loaded = new Map<string, Promise<void>>();

/**
 * Fetch and register a face on demand. The eight families together weigh
 * ~460KB, which is far too much to hand every visitor for a card most of them
 * will render in the default — so only the chosen one is ever fetched, and
 * each is fetched at most once.
 */
export function ensureFont(font: FontEntry): Promise<void> {
  if (font.preloaded) return Promise.resolve();
  const hit = loaded.get(font.id);
  if (hit) return hit;

  const job = (async () => {
    const faces: FontFace[] = [
      new FontFace(font.family, `url(/nd96/fonts/${font.files.display})`, {
        weight: String(font.displayWeight),
        display: "block",
      }),
    ];
    if (font.files.body && font.bodyWeight !== font.displayWeight) {
      faces.push(
        new FontFace(font.family, `url(/nd96/fonts/${font.files.body})`, {
          weight: String(font.bodyWeight),
          display: "block",
        }),
      );
    }
    await Promise.all(
      faces.map(async (face) => {
        await face.load();
        document.fonts.add(face);
      }),
    );
  })().catch((err) => {
    // Let a later attempt retry rather than wedging the picker on this face.
    loaded.delete(font.id);
    throw err;
  });

  loaded.set(font.id, job);
  return job;
}
