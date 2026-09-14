import * as hb from "harfbuzzjs";

// Arabic text for the `/og` card, set with HarfBuzz and drawn as SVG outlines.
//
// WHY NOT SATORI TEXT. Satori measures an Arabic word before it joins the
// letters, so each word's box is as wide as its isolated forms and the gaps
// between words come out ragged; one RTL sentence in one text node also loses
// its word order («هل يحق» → «يحقهل»). The card kit's face, Noto Naskh Arabic,
// does not render there at all: its GSUB crashes Satori's parser, and with the
// offending lookups stripped the letters stop joining (verified 2026-09-14).
// HarfBuzz is the shaper Chromium uses, so an outline set here is the glyph
// run the marketing card renderer draws, and Satori only places an image of a
// known size.
//
// Bidi is the subset a headline needs: the paragraph is RTL; Latin letters and
// digits (ASCII or Arabic-Indic) form LTR runs; a neutral between two LTR
// characters stays LTR («1/5/1445»), every other neutral takes the paragraph's
// direction. Characters the Naskh file lacks — it carries no ASCII at all, not
// even «:» — come from the Latin fallback, as they do from Tahoma on the cards.

export interface ShaperFonts {
  /** Variable Noto Naskh Arabic (wght 400–700). */
  arabic: Uint8Array;
  latinRegular: Uint8Array;
  latinBold: Uint8Array;
}

export interface TextStyle {
  size: number;
  weight: 400 | 500 | 600 | 700;
  color: string;
  /** A multiple of `size`, as in CSS. */
  lineHeight: number;
  maxWidth: number;
  /** CSS `text-wrap: balance`. */
  balance?: boolean;
}

export interface TextImage {
  src: string;
  /** The widest line; every line is right-aligned inside it. */
  width: number;
  /** lines × line box — the box CSS would lay out. */
  height: number;
  /** Extra px above and below `height` in the image, so tall marks never clip. */
  bleed: number;
}

interface Face {
  font: hb.Font;
  upem: number;
  covers: Set<number>;
}

interface Piece {
  face: Face;
  rtl: boolean;
  text: string;
}

interface Run {
  width: number;
  draw: (x: number, baseline: number) => string;
}

function makeFace(bytes: Uint8Array, wght?: number): Face {
  const face = new hb.Face(new hb.Blob(bytes));
  const font = new hb.Font(face);
  if (wght !== undefined) font.setVariations([new hb.Variation("wght", wght)]);
  return { font, upem: face.upem, covers: new Set(face.collectUnicodes()) };
}

const isArabicBlock = (cp: number) =>
  (cp >= 0x0600 && cp <= 0x06ff) ||
  (cp >= 0x0750 && cp <= 0x077f) ||
  (cp >= 0x0870 && cp <= 0x08ff) ||
  (cp >= 0xfb50 && cp <= 0xfdff) ||
  (cp >= 0xfe70 && cp <= 0xfeff);

const isStrongLtr = (cp: number) =>
  (cp >= 0x30 && cp <= 0x39) ||
  (cp >= 0x0660 && cp <= 0x0669) ||
  (cp >= 0x06f0 && cp <= 0x06f9) ||
  (cp >= 0x41 && cp <= 0x5a) ||
  (cp >= 0x61 && cp <= 0x7a) ||
  (cp >= 0xc0 && cp <= 0x24f);

type Bidi = "L" | "R" | "N";
const bidiClass = (cp: number): Bidi =>
  isStrongLtr(cp) ? "L" : isArabicBlock(cp) ? "R" : "N";

export class Shaper {
  private readonly arabicBytes: Uint8Array;
  private readonly naskhByWeight = new Map<number, Face>();
  private readonly latinRegular: Face;
  private readonly latinBold: Face;

  constructor(fonts: ShaperFonts) {
    this.arabicBytes = fonts.arabic;
    this.latinRegular = makeFace(fonts.latinRegular);
    this.latinBold = makeFace(fonts.latinBold);
  }

  private naskh(weight: number): Face {
    let face = this.naskhByWeight.get(weight);
    if (!face) {
      face = makeFace(this.arabicBytes, weight);
      this.naskhByWeight.set(weight, face);
    }
    return face;
  }

  /** One line → direction- and font-homogeneous pieces, in logical order. */
  private pieces(line: string, weight: number): Piece[] {
    const chars = Array.from(line);
    const cls = chars.map((c) => bidiClass(c.codePointAt(0)!));
    const naskh = this.naskh(weight);
    const latin = weight >= 600 ? this.latinBold : this.latinRegular;
    const out: Piece[] = [];
    chars.forEach((ch, i) => {
      let rtl = cls[i] === "R";
      if (cls[i] === "N") {
        let p = i - 1;
        while (p >= 0 && cls[p] === "N") p--;
        let n = i + 1;
        while (n < cls.length && cls[n] === "N") n++;
        rtl = !(p >= 0 && n < cls.length && cls[p] === "L" && cls[n] === "L");
      }
      const cp = ch.codePointAt(0)!;
      // Arabic-block characters, and spaces between Arabic words, stay with
      // Naskh so the word spacing is the kit's; the rest prefer the Latin face.
      const wantsNaskh = isArabicBlock(cp) || (cp === 0x20 && rtl) || !latin.covers.has(cp);
      const face = wantsNaskh && naskh.covers.has(cp) ? naskh : latin;
      const last = out[out.length - 1];
      if (last && last.face === face && last.rtl === rtl) last.text += ch;
      else out.push({ face, rtl, text: ch });
    });
    return out;
  }

  private shape({ face, rtl, text }: Piece, size: number, color: string): Run {
    const buffer = new hb.Buffer();
    buffer.addText(text);
    buffer.guessSegmentProperties();
    buffer.setDirection(rtl ? hb.Direction.RTL : hb.Direction.LTR);
    hb.shape(face.font, buffer);
    const infos = buffer.getGlyphInfos();
    const positions = buffer.getGlyphPositions();
    const k = size / face.upem;
    const advance = positions.reduce((sum, p) => sum + p.xAdvance, 0);
    return {
      width: advance * k,
      draw: (x, baseline) => {
        let pen = 0;
        let paths = "";
        infos.forEach((glyph, i) => {
          const d = face.font.glyphToPath(glyph.codepoint);
          if (d) {
            const gx = x + (pen + positions[i].xOffset) * k;
            const gy = baseline - positions[i].yOffset * k;
            paths += `<path transform="translate(${gx.toFixed(2)} ${gy.toFixed(2)}) scale(${k.toFixed(5)} ${(-k).toFixed(5)})" d="${d}"/>`;
          }
          pen += positions[i].xAdvance;
        });
        return paths ? `<g fill="${color}">${paths}</g>` : "";
      },
    };
  }

  private shapeLine(line: string, style: TextStyle) {
    return this.pieces(line, style.weight).map((piece) => ({
      piece,
      run: this.shape(piece, style.size, style.color),
    }));
  }

  private measure(line: string, style: TextStyle): number {
    return this.shapeLine(line, style).reduce((w, { run }) => w + run.width, 0);
  }

  /** Right-aligned at `right`: RTL pieces march leftwards in logical order; a
   * stretch of LTR pieces is placed as one block, left to right inside it. */
  private drawLine(line: string, style: TextStyle, right: number, baseline: number): string {
    const runs = this.shapeLine(line, style);
    let cursor = right;
    let svg = "";
    for (let i = 0; i < runs.length; ) {
      if (runs[i].piece.rtl) {
        cursor -= runs[i].run.width;
        svg += runs[i].run.draw(cursor, baseline);
        i++;
        continue;
      }
      let j = i;
      let width = 0;
      while (j < runs.length && !runs[j].piece.rtl) width += runs[j++].run.width;
      let x = cursor - width;
      for (let q = i; q < j; q++) {
        svg += runs[q].run.draw(x, baseline);
        x += runs[q].run.width;
      }
      cursor -= width;
      i = j;
    }
    return svg;
  }

  private wrap(words: string[], style: TextStyle, width: number): string[] {
    const lines: string[] = [];
    let current = "";
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (current && this.measure(next, style) > width) {
        lines.push(current);
        current = word;
      } else {
        current = next;
      }
    }
    if (current) lines.push(current);
    return lines;
  }

  private layout(text: string, style: TextStyle): string[] {
    const words = text.trim().split(/\s+/).filter(Boolean);
    const greedy = this.wrap(words, style, style.maxWidth);
    if (!style.balance || greedy.length < 2) return greedy;
    // The narrowest measure that still takes the same number of lines.
    let lo = Math.max(...words.map((w) => this.measure(w, style)));
    let hi = style.maxWidth;
    while (hi - lo > 2) {
      const mid = (lo + hi) / 2;
      if (this.wrap(words, style, mid).length <= greedy.length) hi = mid;
      else lo = mid;
    }
    return this.wrap(words, style, hi);
  }

  render(text: string, style: TextStyle): TextImage {
    const lines = this.layout(text, style);
    const naskh = this.naskh(style.weight);
    const { ascender, descender } = naskh.font.hExtents();
    const k = style.size / naskh.upem;
    const content = (ascender - descender) * k;
    const lineBox = style.size * style.lineHeight;
    // CSS centres the content area (ascent + descent) in the line box.
    const firstBaseline = (lineBox - content) / 2 + ascender * k;
    const bleed = Math.ceil(Math.max(0, content - lineBox) / 2 + style.size * 0.25);
    const width = Math.ceil(Math.max(1, ...lines.map((l) => this.measure(l, style))));
    const height = Math.ceil(lines.length * lineBox);
    const body = lines
      .map((line, i) => this.drawLine(line, style, width, bleed + firstBaseline + i * lineBox))
      .join("");
    const full = height + 2 * bleed;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${full}" viewBox="0 0 ${width} ${full}">${body}</svg>`;
    return {
      src: `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`,
      width,
      height,
      bleed,
    };
  }
}
