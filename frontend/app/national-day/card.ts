// Draws the National Day greeting card onto a 1080×1350 canvas.
//
// WHY CANVAS AND NOT `/og`. The site already shapes Arabic for link unfurls with
// HarfBuzz behind Satori (see app/og/route.tsx) because Satori cannot join
// Arabic on its own. None of that is needed here: this card is drawn in a real
// browser, and the browser's own text engine shapes and joins Arabic correctly
// in `fillText`. So the visitor's card is rendered client-side and downloaded
// as a file — no server round-trip, no name ever leaving the device, and
// nothing stored.
//
// 1080×1350 is the kit's portrait artboard and the 4:5 that WhatsApp, X and
// Instagram all accept without recropping.

import { DEFAULT_FONT, stackFor, type FontEntry } from "./fonts";
import { MOTIF, MOTIF_H, MOTIF_W, type Colourway } from "./motif";

export const CARD_W = 1080;
export const CARD_H = 1350;

/** Longest name we will set. Past this the card stops being a card. */
export const NAME_MAX = 42;
/** The greeting is the visitor's own sentence, so it gets room to be one. */
export const GREETING_MAX = 90;
export const COMPANY_MAX = 46;

/** Offered as the placeholder, and used when the visitor clears the field. */
export const DEFAULT_GREETING = "كل عام والوطن بخير";

/** Set beside the «اليوم الوطني السعودي» band. Latin digits, per the app's
 *  one numeral convention (lib/format/numerals.ts). */
const YEAR = "96";

// Two full motif periods across the card. Two, not three: at three the camels
// fall to ~40px and read as noise; at two they keep their silhouette. A
// non-integer count would break the repeat at the corners.
const PERIODS = 2;
const CELL = CARD_W / (PERIODS * MOTIF_W);
const BAND_H = MOTIF_H * CELL;

const BORDER = 12; // solid accent rule around the whole card
const TICK_Y = 28; // tick row inset from the card edge
const TICK_SIZE = 20;
const BAND_Y = 60; // motif band inset from the card edge

const CONTENT_TOP = BAND_Y + BAND_H + 48;
const CONTENT_BOTTOM = CARD_H - (BAND_Y + BAND_H) - 48;
const CONTENT_X = 112;
const CONTENT_W = CARD_W - CONTENT_X * 2;

export interface CardAssets {
  /** The عزّنا بطبعنا lockup — its own dark field, so it sits on any ground. */
  lockup: HTMLImageElement;
  /** «اليوم الوطني السعودي», white glyphs on alpha, recoloured per card. */
  subline: CanvasImageSource;
}

/** The three lines the visitor owns. Only `name` is required. */
export interface CardFields {
  name: string;
  greeting: string;
  company: string;
}

const LOCKUP_RATIO = 693 / 198;
const SUBLINE_RATIO = 693 / 68;

/**
 * Collapse whitespace, then truncate to `max` at a word boundary where one is
 * near enough. `max` is a parameter, not NAME_MAX: the greeting and the
 * company have their own, longer limits, and clipping all three to the name's
 * would silently eat the end of anyone's sentence.
 */
function clip(raw: string, max: number): string {
  const flat = raw.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  // Prefer the last whole word: «…بن إبراهيم الدوس» is a worse card than
  // «…بن إبراهيم», and a name is the one thing here nobody wants misspelled.
  const space = cut.lastIndexOf(" ");
  return (space > max * 0.6 ? cut.slice(0, space) : cut).trim();
}

export function sanitizeName(raw: string): string {
  return clip(raw, NAME_MAX);
}

/** Paint an alpha-cut PNG in a flat colour, via an offscreen source-in pass. */
function tinted(
  img: CanvasImageSource,
  w: number,
  h: number,
  colour: string,
): HTMLCanvasElement {
  const off = document.createElement("canvas");
  off.width = Math.max(1, Math.round(w));
  off.height = Math.max(1, Math.round(h));
  const c = off.getContext("2d");
  if (!c) return off;
  c.drawImage(img, 0, 0, off.width, off.height);
  c.globalCompositeOperation = "source-in";
  c.fillStyle = colour;
  c.fillRect(0, 0, off.width, off.height);
  return off;
}

// The lockup is stored palette-snapped to exactly five flat values, so the
// checkerboard's bright green is one addressable colour rather than a cloud of
// JPEG-ringed near-greens. That is what makes this remap exact — and it is why
// the asset must never be re-saved through a resampling step.
// Source order: field, checker dark, checker mid, bright green. White — the
// wordmark itself — is the one value never touched.
const EMBLEM_SRC = [
  [0, 38, 39], // #002627
  [3, 27, 31], // #031B1F
  [1, 51, 58], // #01333A
  [1, 137, 75], // #01894B
] as const;
const emblemCache = new Map<string, HTMLCanvasElement>();

function rgbOf(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

/**
 * Repaint the whole emblem tile in the card's hue — field, both checker darks,
 * and the bright squares with their rule — leaving only the white wordmark.
 * Cached per colourway: the remap walks ~137k pixels, which is nothing once but
 * would be paid on every keystroke otherwise.
 */
function emblemIn(img: HTMLImageElement, cw: Colourway): CanvasImageSource {
  const hit = emblemCache.get(cw.id);
  if (hit) return hit;
  const off = document.createElement("canvas");
  off.width = img.naturalWidth;
  off.height = img.naturalHeight;
  const c = off.getContext("2d", { willReadFrequently: true });
  if (!c) return img;
  c.drawImage(img, 0, 0);
  const frame = c.getImageData(0, 0, off.width, off.height);
  const px = frame.data;
  const dst = [...cw.emblem, cw.frame].map(rgbOf);
  for (let i = 0; i < px.length; i += 4) {
    for (let k = 0; k < EMBLEM_SRC.length; k++) {
      const s = EMBLEM_SRC[k];
      if (px[i] === s[0] && px[i + 1] === s[1] && px[i + 2] === s[2]) {
        px[i] = dst[k][0];
        px[i + 1] = dst[k][1];
        px[i + 2] = dst[k][2];
        break;
      }
    }
  }
  c.putImageData(frame, 0, 0);
  emblemCache.set(cw.id, off);
  return off;
}

/** One period of the woven band, laid left to right, optionally flipped. */
function drawMotifBand(
  ctx: CanvasRenderingContext2D,
  y: number,
  colour: string,
  flip: boolean,
) {
  ctx.save();
  ctx.fillStyle = colour;
  if (flip) {
    ctx.translate(0, y + BAND_H);
    ctx.scale(1, -1);
  } else {
    ctx.translate(0, y);
  }
  for (let p = 0; p < PERIODS; p++) {
    const ox = p * MOTIF_W * CELL;
    for (let i = 0; i < MOTIF.length; i += 4) {
      ctx.fillRect(
        ox + MOTIF[i] * CELL,
        MOTIF[i + 1] * CELL,
        // +0.5 closes the hairline seams that fall between rects once the
        // grid is scaled by a non-integer CELL.
        MOTIF[i + 2] * CELL + 0.5,
        MOTIF[i + 3] * CELL + 0.5,
      );
    }
  }
  ctx.restore();
}

/** The ticked rule — the checker row of the weave, and of the 96 lockup. */
function drawTicks(
  ctx: CanvasRenderingContext2D,
  colour: string,
  vertical: boolean,
  along: number,
  from: number,
  to: number,
) {
  ctx.fillStyle = colour;
  const step = TICK_SIZE * 2.4;
  const span = to - from;
  const count = Math.max(1, Math.floor(span / step));
  const gap = span / count;
  for (let i = 0; i < count; i++) {
    const at = from + i * gap + (gap - TICK_SIZE) / 2;
    if (vertical) ctx.fillRect(along, at, TICK_SIZE, TICK_SIZE);
    else ctx.fillRect(at, along, TICK_SIZE, TICK_SIZE);
  }
}

function face(font: FontEntry, weight: number, size: number): string {
  // `scale` normalises each family to Camel's ink height, so switching the
  // font changes the letterforms and not the apparent size.
  return `${weight} ${Math.round(size * font.scale)}px ${stackFor(font)}`;
}

/** Greedy word wrap at the current font. */
function wrap(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    const next = line ? `${line} ${word}` : word;
    // `!line` keeps a single over-long word on its own line rather than
    // dropping it — shrink-to-fit deals with the width.
    if (!line || ctx.measureText(next).width <= maxWidth) line = next;
    else {
      lines.push(line);
      line = word;
    }
  }
  if (line) lines.push(line);
  return lines;
}

interface Fitted {
  size: number;
  lines: string[];
}

/**
 * Step the size down until the text wraps into `maxLines` or fewer. The
 * greeting is the visitor's own sentence now, so it can be a clause rather
 * than two words — it has to be allowed to break, not just shrink to nothing.
 */
function fitLines(
  ctx: CanvasRenderingContext2D,
  font: FontEntry,
  text: string,
  weight: number,
  start: number,
  min: number,
  maxWidth: number,
  maxLines: number,
): Fitted {
  for (let size = start; size >= min; size -= 2) {
    ctx.font = face(font, weight, size);
    const lines = wrap(ctx, text, maxWidth);
    if (lines.length <= maxLines) return { size, lines };
  }
  ctx.font = face(font, weight, min);
  return { size: min, lines: wrap(ctx, text, maxWidth).slice(0, maxLines) };
}

export function drawCard(
  ctx: CanvasRenderingContext2D,
  assets: CardAssets,
  fields: CardFields,
  cw: Colourway,
  font: FontEntry = DEFAULT_FONT,
): void {
  const clean = clip(fields.name, NAME_MAX);
  const greeting = clip(fields.greeting, GREETING_MAX) || DEFAULT_GREETING;
  const company = clip(fields.company, COMPANY_MAX);

  ctx.clearRect(0, 0, CARD_W, CARD_H);
  ctx.fillStyle = cw.ground;
  ctx.fillRect(0, 0, CARD_W, CARD_H);

  // --- frame -------------------------------------------------------------
  ctx.fillStyle = cw.frame;
  ctx.fillRect(0, 0, CARD_W, BORDER);
  ctx.fillRect(0, CARD_H - BORDER, CARD_W, BORDER);
  ctx.fillRect(0, 0, BORDER, CARD_H);
  ctx.fillRect(CARD_W - BORDER, 0, BORDER, CARD_H);

  drawTicks(ctx, cw.frame, false, TICK_Y, BORDER, CARD_W - BORDER);
  drawTicks(
    ctx,
    cw.frame,
    false,
    CARD_H - TICK_Y - TICK_SIZE,
    BORDER,
    CARD_W - BORDER,
  );

  // Both bands run upright. Mirroring the lower one would frame the card
  // symmetrically but hang the camels from the ceiling, which reads as a bug
  // rather than as a border — a woven band keeps its footing on every edge.
  drawMotifBand(ctx, BAND_Y, cw.frame, false);
  drawMotifBand(ctx, CARD_H - BAND_Y - BAND_H, cw.frame, false);

  const sideFrom = BAND_Y + BAND_H;
  const sideTo = CARD_H - BAND_Y - BAND_H;
  drawTicks(ctx, cw.frame, true, TICK_Y, sideFrom, sideTo);
  drawTicks(ctx, cw.frame, true, CARD_W - TICK_Y - TICK_SIZE, sideFrom, sideTo);

  // --- content, as one stack centred in the field ------------------------
  const lockupW = 470;
  const lockupH = lockupW / LOCKUP_RATIO;
  const sublineW = 360;
  const sublineH = sublineW / SUBLINE_RATIO;

  ctx.direction = "rtl";
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";

  const nameFit = fitLines(ctx, font, clean || "…", font.displayWeight, 104, 46, CONTENT_W, 2);
  const companyFit = company
    ? fitLines(ctx, font, company, font.bodyWeight, 40, 26, CONTENT_W, 1)
    : null;
  const greetFit = fitLines(ctx, font, greeting, font.bodyWeight, 56, 30, CONTENT_W, 3);

  // Line box is a multiple of the NOMINAL size, never the scaled one: `scale`
  // exists precisely so every face has the same ink height at a given
  // nominal, so multiplying by it again would give Arabic Poetry a line box
  // 2.1x taller than its letters and push the card through its own border.
  const LH = 1.34;
  const nameH = nameFit.lines.length * nameFit.size * LH;
  const companyH = companyFit ? companyFit.size * LH : 0;
  const greetH = greetFit.lines.length * greetFit.size * LH;
  const domainSize = 30;

  // Gaps are the only elastic part. A visitor who fills every field and writes
  // a three-line greeting gets tighter spacing, but the type never shrinks
  // below the size fitLines already settled on — legibility outranks rhythm.
  const gaps = {
    lockupSub: 30,
    subName: 104,
    nameCompany: companyFit ? 14 : 0,
    blockGreet: 46,
    greetDomain: 108,
  };
  const fixed =
    lockupH + sublineH + nameH + companyH + greetH + domainSize * LH;
  const wanted = Object.values(gaps).reduce((a, b) => a + b, 0);
  const room = CONTENT_BOTTOM - CONTENT_TOP - fixed;
  const squeeze = wanted > room ? Math.max(0.35, room / wanted) : 1;
  const g = (k: keyof typeof gaps) => gaps[k] * squeeze;

  const stackH = fixed + wanted * squeeze;
  let y =
    CONTENT_TOP + Math.max(0, (CONTENT_BOTTOM - CONTENT_TOP - stackH) / 2);
  const mid = CARD_W / 2;

  ctx.drawImage(
    emblemIn(assets.lockup, cw),
    mid - lockupW / 2,
    y,
    lockupW,
    lockupH,
  );
  y += lockupH + g("lockupSub");

  // «اليوم الوطني السعودي» is a raster cut from the official artboard, so the
  // 96 cannot be baked into it — it is set alongside, in the card's own face.
  // Reading order is RTL, so the year sits at the LEFT end of the pair, and
  // the two are centred as one block rather than each on its own.
  ctx.font = face(font, font.displayWeight, Math.round(sublineH));
  const yearW = ctx.measureText(YEAR).width;
  const yearGap = Math.round(sublineH * 0.34);
  const pairW = sublineW + yearGap + yearW;
  const pairRight = mid + pairW / 2;

  ctx.drawImage(
    tinted(assets.subline, sublineW * 2, sublineH * 2, cw.ink),
    pairRight - sublineW,
    y,
    sublineW,
    sublineH,
  );

  ctx.save();
  ctx.direction = "ltr";
  ctx.textAlign = "left";
  ctx.fillStyle = cw.ink;
  // 0.765 of the crop is where the Arabic baseline falls inside it: the glyph
  // bodies occupy y 5..52 of the 68px source band and the descenders 54..63,
  // so centring the digits on the box instead would float them high.
  ctx.fillText(YEAR, pairRight - sublineW - yearGap - yearW, y + sublineH * 0.765);
  ctx.restore();

  y += sublineH + g("subName");

  ctx.fillStyle = cw.ink;
  const run = (fit: Fitted, weight: number, alpha = 1) => {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.font = face(font, weight, fit.size);
    for (const line of fit.lines) {
      const step = fit.size * LH;
      y += step;
      // Sit the baseline inside its line box rather than on the bottom edge.
      ctx.fillText(line, mid, y - step * (1 - 1 / LH) * 0.5);
    }
    ctx.restore();
  };

  // An empty name still reserves its block, so the card does not jump around
  // under the visitor as they type the first letter.
  if (clean) run(nameFit, font.displayWeight);
  else y += nameH;

  if (companyFit) {
    y += g("nameCompany");
    run(companyFit, font.bodyWeight, 0.78);
  }

  y += g("blockGreet");
  run(greetFit, font.bodyWeight);
  y += g("greetDomain");

  // The domain is Latin, so it is set LTR in the UI face, not the Arabic one.
  // The Rayhan lockup used to sit above it and was removed: the card now
  // carries the domain alone, leaving the visitor's own company as the only
  // mark on it besides the National Day emblem.
  ctx.save();
  ctx.direction = "ltr";
  ctx.globalAlpha = 0.72;
  ctx.font = `500 ${domainSize}px "ND96 Domain", Tahoma, system-ui, sans-serif`;
  ctx.fillText("rayhanai.com", mid, y + domainSize);
  ctx.restore();
}
