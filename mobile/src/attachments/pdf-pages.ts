// F2 — PDF page-count ESTIMATE for the OCR quota gate (memory
// project_ocr_gate_page_count): the web reads pdfjs `numPages`; the phone
// counts page objects in the raw bytes — `/Type /Page`, never `/Type /Pages`
// (the page-tree nodes). Pages inside compressed object streams are invisible
// to a byte scan → 0 found → the 1-page floor, exactly what the server assumes
// without a count. The post-OCR settle bills the true number either way.
//
// Streaming: `feed()` takes the file in slices (a 50 MB PDF is never held in
// memory at once); a match split across two slices is still counted once.
// Pure — `npm test` covers it.

const SLASH = 0x2f;
const TYPE = [0x54, 0x79, 0x70, 0x65]; // Type
const PAGE = [0x50, 0x61, 0x67, 0x65]; // Page
/** Whitespace tolerated between `/Type` and `/Page` (PDF allows any amount;
 *  real writers emit 0–1). Bounds the bytes carried between slices. */
const MAX_GAP = 8;
/** Longest match incl. the look-ahead byte: `/Type` + gap + `/Page` + 1. */
const CARRY = 5 + MAX_GAP + 5 + 1;

function isWhitespace(b: number): boolean {
  return b === 0x20 || b === 0x0a || b === 0x0d || b === 0x09 || b === 0x0c || b === 0x00;
}

/** PDF "regular" byte: a name continues (`/Pages`, `/PageLabel`). Delimiters
 *  and whitespace end it. */
function continuesName(b: number): boolean {
  if (isWhitespace(b)) return false;
  // ( ) < > [ ] { } / %
  return !(b === 0x28 || b === 0x29 || b === 0x3c || b === 0x3e || b === 0x5b || b === 0x5d || b === 0x7b || b === 0x7d || b === 0x2f || b === 0x25);
}

function matchesAt(buf: Uint8Array, i: number, word: number[]): boolean {
  for (let k = 0; k < word.length; k++) if (buf[i + k] !== word[k]) return false;
  return true;
}

/** -1 = no match at i; 0 = undecidable (ran out of bytes); 1 = a page object. */
function pageAt(buf: Uint8Array, i: number, final: boolean): -1 | 0 | 1 {
  const n = buf.length;
  if (buf[i] !== SLASH) return -1;
  if (i + 5 > n) return final ? -1 : 0;
  if (!matchesAt(buf, i + 1, TYPE)) return -1;
  let j = i + 5;
  let gap = 0;
  while (j < n && isWhitespace(buf[j]) && gap <= MAX_GAP) {
    j++;
    gap++;
  }
  if (gap > MAX_GAP) return -1;
  if (j + 5 > n) return final ? -1 : 0;
  if (buf[j] !== SLASH || !matchesAt(buf, j + 1, PAGE)) return -1;
  const after = j + 5;
  if (after >= n) return final ? 1 : 0;
  return continuesName(buf[after]) ? -1 : 1;
}

export interface PdfPageCounter {
  feed(slice: Uint8Array): void;
  /** Page objects found; at least 1 (the server's floor). */
  end(): number;
}

export function createPdfPageCounter(): PdfPageCounter {
  let carry = new Uint8Array(0);
  let pages = 0;

  const scan = (buf: Uint8Array, final: boolean): number => {
    // Positions in the last CARRY bytes are left for the next slice (their
    // match may continue there) unless this is the end of the file.
    const limit = final ? buf.length : Math.max(0, buf.length - CARRY);
    for (let i = 0; i < limit; i++) {
      if (buf[i] === SLASH && pageAt(buf, i, final) === 1) pages++;
    }
    return limit;
  };

  return {
    feed(slice) {
      if (slice.length === 0) return;
      const buf = new Uint8Array(carry.length + slice.length);
      buf.set(carry, 0);
      buf.set(slice, carry.length);
      const scanned = scan(buf, false);
      carry = buf.slice(scanned);
    },
    end() {
      scan(carry, true);
      carry = new Uint8Array(0);
      return Math.max(1, pages);
    },
  };
}

/** One-shot helper (tests, small files). */
export function countPdfPages(bytes: Uint8Array): number {
  const counter = createPdfPageCounter();
  counter.feed(bytes);
  return counter.end();
}
