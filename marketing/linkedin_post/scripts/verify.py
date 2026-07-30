#!/usr/bin/env python3
"""
verify.py — Step 4b: rasterize deck.pdf -> previews/ and check geometry.

Renders every PDF page to previews/slide_NN.png (via PyMuPDF / fitz) so the
agent can Read each slide and eyeball it for overflow, clipping, or tofu boxes.
Also asserts the page geometry is the 4:5 LinkedIn portrait carousel size.

This is the RELIABLE verify path — Playwright MCP screenshots proved flaky here.
(True DOM-overflow measurement still needs a browser: serve the folder with
`python -m http.server` and measure scrollHeight-clientHeight per .slide. This
script does the practical visual check instead.)

Usage:
    python scripts/verify.py <short>            # verifies decks/<short>/deck.pdf
    python scripts/verify.py <short> --dpi 96

Exit code is non-zero if the PDF is missing or the geometry is wrong.
"""
import sys
import pathlib
import argparse

import fitz  # PyMuPDF

ROOT = pathlib.Path(__file__).resolve().parent.parent          # marketing/linkedin_post/

# 1080x1350 @ 96dpi is the LinkedIn portrait target. PDF points are 72dpi, so
# the page box should read 810 x 1012.5 pt. Allow a rounding tolerance.
EXPECT_W, EXPECT_H, TOL = 810.0, 1012.5, 2.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("short", help="deck folder name (first 8 chars of token)")
    ap.add_argument("--pdf", default="deck.pdf")
    ap.add_argument("--dpi", type=int, default=72, help="raster dpi for previews")
    args = ap.parse_args()

    deck_dir = ROOT / "decks" / args.short
    pdf_path = deck_dir / args.pdf
    if not pdf_path.exists():
        print(f"FAIL: no PDF at {pdf_path}", file=sys.stderr)
        return 2

    prev = deck_dir / "previews"
    prev.mkdir(exist_ok=True)

    doc = fitz.open(pdf_path)
    w, h = doc[0].rect.width, doc[0].rect.height
    geom_ok = abs(w - EXPECT_W) <= TOL and abs(h - EXPECT_H) <= TOL

    for i in range(len(doc)):
        doc[i].get_pixmap(dpi=args.dpi).save(prev / f"slide_{i + 1:02d}.png")

    status = "ok" if geom_ok else "BAD-GEOMETRY"
    print(f"{status}: {args.short}  {len(doc)} pages  {round(w)}x{round(h)}pt "
          f"(expect {round(EXPECT_W)}x{round(EXPECT_H)})")
    print(f"  -> {prev}/slide_01..{len(doc):02d}.png  @ {args.dpi}dpi")
    if not (6 <= len(doc) <= 10):
        print(f"  warn: {len(doc)} slides is outside the 6–10 sweet spot")
    return 0 if geom_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
