"""Build ``backend/app/assets/export/reference.docx`` — the Word export stylesheet.

Reproducible: starts from pandoc's OWN default reference.docx (the one bundled
with the pinned ``pypandoc_binary``) and patches a handful of XML parts. No
python-docx, no hand-edited binary in the repo without its recipe.

    python backend/scripts/build_export_reference_docx.py

What the patch sets (document_export_pdf_word.md § Word):

* Font: every theme-font ``w:rFonts`` (body, headings, title) becomes explicit
  «IBM Plex Sans Arabic» for all four script slots. ``fontTable.xml`` declares
  the face with ``w:altName="Arial"`` — Word's own substitution hint — so a
  machine without Plex falls back to Arial, which has full Arabic coverage.
  Code styles keep Consolas.
* Direction: ``w:bidi`` in the default paragraph properties, Arabic as the
  default/bidi language, an RTL (``w:bidi``) section.
* Page: A4, 2cm margins, a footer «صفحة n من m» built from PAGE / NUMPAGES
  fields (Latin digits — latin-numerals policy). Pandoc keeps the reference
  document's section properties, header and footer.
* Headings: black, bold (``w:b`` + ``w:bCs`` — Arabic runs are complex-script,
  so ``w:b`` alone would not bold them), never italic. No brand colour.
* Table style ``Table`` (the one pandoc assigns): single borders on every edge
  and inside line, RTL column order (``w:bidiVisual``), bold shaded header row.

The output is written with fixed zip timestamps so re-running the script on the
same pandoc version yields byte-identical bytes.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "backend" / "app" / "assets" / "export" / "reference.docx"

FONT = "IBM Plex Sans Arabic"
FONT_FALLBACK = "Arial"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FOOTER_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
)
FOOTER_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"
)
FOOTER_REL_ID = "rIdLunaFooter1"

# Heading sizes in half-points (w:sz). Body is 24 (12pt).
HEADING_SIZES = {1: 32, 2: 28, 3: 26, 4: 24, 5: 24, 6: 24, 7: 24, 8: 24, 9: 24}

EXPLICIT_RFONTS = (
    f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" '
    f'w:eastAsia="{FONT}" w:cs="{FONT}" />'
)


def _pandoc_path() -> str:
    import pypandoc  # dev-time dependency (pypandoc_binary, pinned in requirements)

    return pypandoc.get_pandoc_path()


def _default_reference_docx() -> bytes:
    return subprocess.run(
        [_pandoc_path(), "--print-default-data-file", "reference.docx"],
        check=True,
        capture_output=True,
    ).stdout


# ---------------------------------------------------------------------------
# styles.xml
# ---------------------------------------------------------------------------

_STYLE_BLOCK_RE = re.compile(r"<w:style\b[^>]*>.*?</w:style>", re.S)
_STYLE_ID_RE = re.compile(r'w:styleId="([^"]+)"')
_THEME_RFONTS_RE = re.compile(r"<w:rFonts\b[^>]*Theme=[^>]*/>", re.S)
_COLOR_RE = re.compile(r"<w:color\b[^>]*/>", re.S)
_ITALIC_RE = re.compile(r"\s*<w:i\s*/>")
_SZ_RE = re.compile(r'<w:sz w:val="\d+" />')
_SZCS_RE = re.compile(r'<w:szCs w:val="\d+" />')

_TABLE_STYLE = f"""<w:style w:type="table" w:default="1" w:styleId="Table">
    <w:name w:val="Table" />
    <w:basedOn w:val="TableNormal" />
    <w:semiHidden />
    <w:unhideWhenUsed />
    <w:qFormat />
    <w:pPr>
      <w:bidi />
      <w:spacing w:before="40" w:after="40" />
    </w:pPr>
    <w:tblPr>
      <w:bidiVisual />
      <w:tblInd w:w="0" w:type="dxa" />
      <w:tblBorders>
        <w:top w:val="single" w:sz="4" w:space="0" w:color="444444" />
        <w:left w:val="single" w:sz="4" w:space="0" w:color="444444" />
        <w:bottom w:val="single" w:sz="4" w:space="0" w:color="444444" />
        <w:right w:val="single" w:sz="4" w:space="0" w:color="444444" />
        <w:insideH w:val="single" w:sz="4" w:space="0" w:color="444444" />
        <w:insideV w:val="single" w:sz="4" w:space="0" w:color="444444" />
      </w:tblBorders>
      <w:tblCellMar>
        <w:top w:w="40" w:type="dxa" />
        <w:left w:w="108" w:type="dxa" />
        <w:bottom w:w="40" w:type="dxa" />
        <w:right w:w="108" w:type="dxa" />
      </w:tblCellMar>
    </w:tblPr>
    <w:tblStylePr w:type="firstRow">
      <w:rPr>
        <w:b />
        <w:bCs />
      </w:rPr>
      <w:tcPr>
        <w:shd w:val="clear" w:color="auto" w:fill="F0F0F0" />
        <w:vAlign w:val="bottom" />
      </w:tcPr>
    </w:tblStylePr>
  </w:style>"""


def _patch_heading(block: str, level: int) -> str:
    block = _COLOR_RE.sub('<w:color w:val="000000" />', block)
    block = _ITALIC_RE.sub("", block)
    size = HEADING_SIZES[level]
    block = _SZ_RE.sub(f'<w:sz w:val="{size}" />', block)
    block = _SZCS_RE.sub(f'<w:szCs w:val="{size}" />', block)
    if "<w:bCs" not in block:
        # rPr child order: rStyle, rFonts, b, bCs, i, ... — b/bCs right after rFonts.
        block = block.replace(EXPLICIT_RFONTS, EXPLICIT_RFONTS + "<w:b /><w:bCs />", 1)
    return block


def _patch_title(block: str) -> str:
    block = _COLOR_RE.sub('<w:color w:val="000000" />', block)
    block = _SZ_RE.sub('<w:sz w:val="36" />', block)
    block = _SZCS_RE.sub('<w:szCs w:val="36" />', block)
    if "<w:bCs" not in block:
        block = block.replace(EXPLICIT_RFONTS, EXPLICIT_RFONTS + "<w:b /><w:bCs />", 1)
    return block


def patch_styles(xml: str) -> str:
    # Fonts first, so the heading patch can anchor on the explicit rFonts.
    xml = _THEME_RFONTS_RE.sub(EXPLICIT_RFONTS, xml)

    # docDefaults: RTL paragraphs, Arabic language.
    xml, n = re.subn(
        r'<w:lang w:val="en-US"(\s+w:eastAsia="[^"]*")\s+w:bidi="[^"]*"\s*/>',
        r'<w:lang w:val="ar-SA"\1 w:bidi="ar-SA" />',
        xml,
        count=1,
    )
    if n != 1:
        raise RuntimeError("rPrDefault w:lang anchor not found (pandoc default changed)")
    xml, n = re.subn(
        r"(<w:pPrDefault>\s*<w:pPr>)",
        r"\1<w:bidi />",
        xml,
        count=1,
    )
    if n != 1:
        raise RuntimeError("pPrDefault anchor not found (pandoc default changed)")

    def _patch_block(match: re.Match) -> str:
        block = match.group(0)
        sid = _STYLE_ID_RE.search(block)
        if not sid:
            return block
        style_id = sid.group(1)
        m = re.fullmatch(r"Heading([1-9])(Char)?", style_id)
        if m:
            return _patch_heading(block, int(m.group(1)))
        if style_id in ("Title", "TitleChar"):
            return _patch_title(block)
        if style_id == "Table":
            return _TABLE_STYLE
        if style_id == "Hyperlink":
            return _COLOR_RE.sub('<w:color w:val="1F3A5F" />', block)
        return block

    xml = _STYLE_BLOCK_RE.sub(_patch_block, xml)
    if "<w:bidiVisual />" not in xml:
        raise RuntimeError("Table style anchor not found (pandoc default changed)")
    return xml


# ---------------------------------------------------------------------------
# fontTable.xml / settings.xml
# ---------------------------------------------------------------------------


def patch_font_table(xml: str) -> str:
    entry = (
        f'  <w:font w:name="{FONT}">\n'
        f'    <w:altName w:val="{FONT_FALLBACK}"/>\n'
        '    <w:charset w:val="B2"/>\n'
        '    <w:family w:val="swiss"/>\n'
        '    <w:pitch w:val="variable"/>\n'
        "  </w:font>\n"
    )
    return xml.replace("</w:fonts>", entry + "</w:fonts>", 1)


def patch_settings(xml: str) -> str:
    return xml.replace(
        '<w:themeFontLang w:val="en-US" />',
        '<w:themeFontLang w:val="en-US" w:bidi="ar-SA" />',
        1,
    )


# ---------------------------------------------------------------------------
# document.xml (section properties) + footer part
# ---------------------------------------------------------------------------

_FOOTER_RUN_PR = '<w:rPr><w:rtl /><w:sz w:val="18" /><w:szCs w:val="18" /></w:rPr>'


def _field(instr: str) -> str:
    return (
        f'<w:r>{_FOOTER_RUN_PR}<w:fldChar w:fldCharType="begin" /></w:r>'
        f'<w:r>{_FOOTER_RUN_PR}<w:instrText xml:space="preserve"> {instr} \\* Arabic </w:instrText></w:r>'
        f'<w:r>{_FOOTER_RUN_PR}<w:fldChar w:fldCharType="separate" /></w:r>'
        f'<w:r>{_FOOTER_RUN_PR}<w:t>1</w:t></w:r>'
        f'<w:r>{_FOOTER_RUN_PR}<w:fldChar w:fldCharType="end" /></w:r>'
    )


def _text(t: str) -> str:
    return f'<w:r>{_FOOTER_RUN_PR}<w:t xml:space="preserve">{t}</w:t></w:r>'


FOOTER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
    '<w:p><w:pPr><w:bidi /><w:jc w:val="center" /></w:pPr>'
    + _text("صفحة ")
    + _field("PAGE")
    + _text(" من ")
    + _field("NUMPAGES")
    + "</w:p></w:ftr>"
)

DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
    "<w:body>"
    '<w:p><w:pPr><w:bidi /></w:pPr></w:p>'
    "<w:sectPr>"
    f'<w:footerReference w:type="default" r:id="{FOOTER_REL_ID}" />'
    '<w:footnotePr><w:numRestart w:val="eachSect" /></w:footnotePr>'
    # A4 portrait, 2cm (1134 twips) margins.
    '<w:pgSz w:w="11906" w:h="16838" />'
    '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
    'w:header="567" w:footer="567" w:gutter="0" />'
    "<w:bidi />"
    "</w:sectPr>"
    "</w:body></w:document>"
)


def patch_document_rels(xml: str) -> str:
    rel = (
        f'<Relationship Type="{FOOTER_REL_TYPE}" Id="{FOOTER_REL_ID}" '
        'Target="footer1.xml" />'
    )
    # The default reference doc carries a sample hyperlink rel; drop it with the body.
    xml = re.sub(r'<Relationship [^>]*TargetMode="External" />', "", xml)
    return xml.replace("</Relationships>", rel + "</Relationships>", 1)


def patch_content_types(xml: str) -> str:
    override = (
        f'<Override PartName="/word/footer1.xml" ContentType="{FOOTER_CONTENT_TYPE}" />'
    )
    return xml.replace("</Types>", override + "</Types>", 1)


# ---------------------------------------------------------------------------


def build(source: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(source))
    parts: dict[str, bytes] = {name: src.read(name) for name in src.namelist()}

    def text(name: str) -> str:
        return parts[name].decode("utf-8")

    parts["word/styles.xml"] = patch_styles(text("word/styles.xml")).encode("utf-8")
    parts["word/fontTable.xml"] = patch_font_table(text("word/fontTable.xml")).encode("utf-8")
    parts["word/settings.xml"] = patch_settings(text("word/settings.xml")).encode("utf-8")
    parts["word/document.xml"] = DOCUMENT_XML.encode("utf-8")
    parts["word/footer1.xml"] = FOOTER_XML.encode("utf-8")
    parts["word/_rels/document.xml.rels"] = patch_document_rels(
        text("word/_rels/document.xml.rels")
    ).encode("utf-8")
    parts["[Content_Types].xml"] = patch_content_types(
        text("[Content_Types].xml")
    ).encode("utf-8")

    order = ["[Content_Types].xml"] + sorted(n for n in parts if n != "[Content_Types].xml")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in order:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, parts[name])
    return out.getvalue()


def main() -> int:
    data = build(_default_reference_docx())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(data)
    print(f"wrote {OUT_PATH} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
