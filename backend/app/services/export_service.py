"""Document export — markdown → PDF (WeasyPrint) / Word (pandoc).

Plan: ``.claude/plans/document_export_pdf_word.md``.

Pipeline (both formats share step 1, so a PDF and a .docx of the same text can
never disagree about structure):

1. ``markdown_to_html`` — ONE converter (markdown-it-py, already in the lock),
   CommonMark + GFM tables + strikethrough. Raw HTML is OFF (escaped) and images
   are rendered as their alt text: the input is user/LLM text, and both
   renderers would otherwise fetch whatever ``<img src>`` / ``<link href>`` it
   names (SSRF / local file read).
2. PDF: WeasyPrint with an RTL print stylesheet and the committed IBM Plex Sans
   Arabic TTFs. The URL fetcher allows ONLY those font files.
   Word: the bundled pandoc binary (``pypandoc_binary``), ``--sandbox``, the
   committed ``assets/export/reference.docx`` (built by
   ``backend/scripts/build_export_reference_docx.py``), ``-M dir=rtl -M lang=ar``.

Both renderers are imported LAZILY inside the render functions. WeasyPrint
loads pango/harfbuzz through cffi at import time; on a box without them (the
Windows dev machine, or an image missing the apt packages) that import raises
``OSError`` — which must break export only (→ ``ExportUnavailableError`` → 503),
never ``backend.app.main``.

Everything here is synchronous and CPU-bound; the route runs it in a worker
thread and bounds concurrency with ``RENDER_CONCURRENCY``.
"""
from __future__ import annotations

import html
import logging
import re
import subprocess
import unicodedata
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

ExportFormat = Literal["pdf", "docx"]

# ---------------------------------------------------------------------------
# Limits (the route enforces these; kept here so tests and route agree)
# ---------------------------------------------------------------------------

MAX_TITLE_CHARS = 200
MAX_MARKDOWN_CHARS = 400_000

# Concurrent renders per worker process. Rendering is pure CPU; more than this
# just makes every caller slower and can starve the event loop's threadpool.
RENDER_CONCURRENCY = 2

# Hard wall for one pandoc run. WeasyPrint has no timeout knob; it is bounded by
# MAX_MARKDOWN_CHARS + the per-user rate limit instead.
PANDOC_TIMEOUT_SECONDS = 60

MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
FONT_REGULAR = FONTS_DIR / "IBMPlexSansArabic-Regular.ttf"
FONT_BOLD = FONTS_DIR / "IBMPlexSansArabic-Bold.ttf"
REFERENCE_DOCX = ASSETS_DIR / "export" / "reference.docx"


class ExportUnavailableError(RuntimeError):
    """A renderer (or its native libs / binary) is missing or failed → HTTP 503."""


# ---------------------------------------------------------------------------
# Filename
# ---------------------------------------------------------------------------

_FILENAME_FORBIDDEN_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WS_RE = re.compile(r"\s+")
MAX_FILENAME_STEM_CHARS = 100


def sanitize_filename_stem(title: str) -> str:
    """Title → a filesystem-safe stem (Arabic kept). Empty → ``document``."""
    stem = unicodedata.normalize("NFC", title or "")
    # Drop bidi controls / format chars (Cf): they can visually reverse an
    # extension in a file manager ("‮fdp.exe").
    stem = "".join(ch for ch in stem if unicodedata.category(ch) != "Cf")
    stem = _FILENAME_FORBIDDEN_RE.sub(" ", stem)
    stem = _WS_RE.sub(" ", stem).strip().strip(".").strip()
    stem = stem[:MAX_FILENAME_STEM_CHARS].strip()
    return stem or "document"


def content_disposition(title: str, ext: str) -> str:
    """``attachment`` with an ASCII fallback and an RFC 5987 UTF-8 ``filename*``."""
    name = f"{sanitize_filename_stem(title)}.{ext}"
    return (
        f'attachment; filename="document.{ext}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )


# ---------------------------------------------------------------------------
# Markdown → HTML (the ONE converter)
# ---------------------------------------------------------------------------

_md_parser = None


def _parser():
    global _md_parser
    if _md_parser is None:
        from markdown_it import MarkdownIt

        # breaks=True: the payload is the «نسخ» text, which lawyers paste as
        # plain text where every newline is a line break — and the «المراجع»
        # block is newline-separated `n-label` lines that would otherwise
        # collapse into one run-on paragraph.
        md = MarkdownIt(
            "commonmark",
            {"html": False, "linkify": False, "typographer": False, "breaks": True},
        ).enable(["table", "strikethrough"])

        def _image_as_text(self, tokens, idx, options, env):  # noqa: ANN001
            # Never emit <img>: both renderers would fetch the src.
            token = tokens[idx]
            alt = self.renderInline(token.children or [], options, env)
            return alt

        md.add_render_rule("image", _image_as_text)
        _md_parser = md
    return _md_parser


def _starts_with_h1(tokens) -> bool:  # noqa: ANN001
    for tok in tokens:
        if tok.type == "heading_open":
            return tok.tag == "h1"
        if tok.block and tok.nesting != -1:
            return False
    return False


# The block ``appendReferencesForCopy`` (frontend ReferencePanel.tsx) appends:
#   "<body>\n\nالمراجع\n1-label\n2-label"   (or the block alone for an empty body)
# Its bare «المراجع» line is a heading in the copy's intent; promote the LAST
# such line to one so it reads as a section in the file. Nothing else about the
# block is touched — the server never re-derives references.
_REFS_BLOCK_RE = re.compile(r"(?:^|\n\n)المراجع\n(?=\d+-)")


def _promote_references_heading(markdown: str) -> str:
    last = None
    for last in _REFS_BLOCK_RE.finditer(markdown):
        pass
    if last is None:
        return markdown
    lead = "\n\n" if last.group(0).startswith("\n") else ""
    return markdown[: last.start()] + f"{lead}## المراجع\n\n" + markdown[last.end():]


def markdown_to_html(markdown: str, title: str) -> str:
    """Body HTML fragment. ``title`` becomes an <h1> unless the body opens with one."""
    md = _parser()
    tokens = md.parse(_promote_references_heading(markdown or ""))
    body = md.renderer.render(tokens, md.options, {})
    clean_title = (title or "").strip()
    if clean_title and not _starts_with_h1(tokens):
        body = f"<h1>{html.escape(clean_title)}</h1>\n{body}"
    return body


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_PRINT_CSS = """
@font-face {{
  font-family: "IBM Plex Sans Arabic";
  src: url("{regular}") format("truetype");
  font-weight: 400;
  font-style: normal;
}}
@font-face {{
  font-family: "IBM Plex Sans Arabic";
  src: url("{bold}") format("truetype");
  font-weight: 700;
  font-style: normal;
}}
@page {{
  size: A4;
  margin: 2cm 2cm 2.2cm 2cm;
  @bottom-center {{
    content: "صفحة " counter(page) " من " counter(pages);
    font-family: "IBM Plex Sans Arabic", "DejaVu Sans", sans-serif;
    font-size: 9pt;
    color: #555;
    direction: rtl;
  }}
}}
html {{ direction: rtl; }}
body {{
  font-family: "IBM Plex Sans Arabic", "DejaVu Sans", sans-serif;
  font-size: 12pt;
  line-height: 1.75;
  color: #111;
  text-align: right;
  margin: 0;
}}
h1, h2, h3, h4, h5, h6 {{
  font-weight: 700;
  line-height: 1.4;
  margin: 1.1em 0 0.45em;
  page-break-after: avoid;
  break-after: avoid;
  page-break-inside: avoid;
}}
h1 {{ font-size: 18pt; margin-top: 0; }}
h2 {{ font-size: 15pt; }}
h3 {{ font-size: 13.5pt; }}
h4, h5, h6 {{ font-size: 12pt; }}
p {{ margin: 0 0 0.6em; orphans: 3; widows: 3; }}
strong, b, th {{ font-weight: 700; }}
ul, ol {{ margin: 0 0 0.6em; padding-right: 1.6em; padding-left: 0; }}
li {{ margin: 0.15em 0; }}
li > p {{ margin: 0; }}
blockquote {{
  margin: 0.6em 0;
  padding: 0 0.9em;
  border-right: 3pt solid #bbb;
  color: #333;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 0.8em 0;
  font-size: 11pt;
  line-height: 1.5;
}}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; break-inside: avoid; }}
th, td {{
  border: 0.75pt solid #444;
  padding: 4pt 6pt;
  text-align: right;
  vertical-align: top;
}}
th {{ background: #f0f0f0; }}
code {{
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 0.9em;
  background: #f4f4f4;
  padding: 0 2pt;
}}
pre {{
  direction: ltr;
  text-align: left;
  white-space: pre-wrap;
  word-wrap: break-word;
  background: #f4f4f4;
  border: 0.5pt solid #ddd;
  padding: 6pt 8pt;
  font-size: 9.5pt;
  line-height: 1.45;
}}
pre code {{ background: none; padding: 0; font-size: inherit; }}
hr {{ border: 0; border-top: 0.75pt solid #999; margin: 1em 0; }}
a {{ color: inherit; text-decoration: underline; }}
"""


def build_pdf_html(markdown: str, title: str) -> str:
    css = _PRINT_CSS.format(regular=FONT_REGULAR.as_uri(), bold=FONT_BOLD.as_uri())
    body = markdown_to_html(markdown, title)
    doc_title = html.escape((title or "").strip() or "document")
    return (
        '<!DOCTYPE html>\n<html dir="rtl" lang="ar">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{doc_title}</title>\n<style>{css}</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _import_weasyprint():
    try:
        import weasyprint  # noqa: PLC0415 — lazy on purpose (native libs)
    except (ImportError, OSError) as exc:
        logger.error("export: WeasyPrint unavailable: %s", exc)
        raise ExportUnavailableError("weasyprint unavailable") from exc
    return weasyprint


def render_pdf(markdown: str, title: str) -> bytes:
    weasyprint = _import_weasyprint()
    allowed = {FONT_REGULAR.as_uri(), FONT_BOLD.as_uri()}

    class _AssetsOnlyFetcher(weasyprint.URLFetcher):
        """Serve the committed fonts; refuse every other URL (no SSRF, no file read)."""

        def fetch(self, url, headers=None):  # noqa: ANN001
            if url.split("?", 1)[0] not in allowed:
                raise ValueError("export: external resource refused")
            return super().fetch(url, headers)

    try:
        document = weasyprint.HTML(
            string=build_pdf_html(markdown, title),
            base_url=str(ASSETS_DIR),
            url_fetcher=_AssetsOnlyFetcher(),
        )
        pdf: Optional[bytes] = document.write_pdf()
    except ExportUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — any renderer fault → 503, logged
        logger.exception("export: WeasyPrint render failed")
        raise ExportUnavailableError("pdf render failed") from exc
    if not pdf:
        raise ExportUnavailableError("pdf render returned nothing")
    return pdf


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


def _pandoc_path() -> str:
    try:
        import pypandoc  # noqa: PLC0415 — lazy on purpose

        return pypandoc.get_pandoc_path()
    except Exception as exc:  # ImportError, or OSError("No pandoc was found")
        logger.error("export: pandoc unavailable: %s", exc)
        raise ExportUnavailableError("pandoc unavailable") from exc


_TBLPR_RE = re.compile(r"<w:tblPr>(.*?)</w:tblPr>", re.S)
_TBLSTYLE_RE = re.compile(r"<w:tblStyle\b[^>]*/>")
_TBLW_AUTO_RE = re.compile(r'<w:tblW w:type="auto" w:w="0"\s*/>')


def _rtl_tables(document_xml: str) -> str:
    """RTL column order + full width on every table pandoc emitted.

    The reference style also sets ``w:bidiVisual``, but not every consumer
    (LibreOffice, Google Docs) resolves table-style tblPr, so it is stamped on
    each table directly. Element order follows CT_TblPr: tblStyle → bidiVisual
    → tblW.
    """

    def _fix(match: re.Match) -> str:
        inner = match.group(1)
        if "<w:bidiVisual" not in inner:
            style = _TBLSTYLE_RE.search(inner)
            if style:
                inner = inner[: style.end()] + "<w:bidiVisual />" + inner[style.end():]
            else:
                inner = "<w:bidiVisual />" + inner
        inner = _TBLW_AUTO_RE.sub('<w:tblW w:type="pct" w:w="5000" />', inner)
        return f"<w:tblPr>{inner}</w:tblPr>"

    return _TBLPR_RE.sub(_fix, document_xml)


def _postprocess_docx(data: bytes) -> bytes:
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            payload = src.read(info.filename)
            if info.filename == "word/document.xml":
                payload = _rtl_tables(payload.decode("utf-8")).encode("utf-8")
            dst.writestr(info, payload)
    return out.getvalue()


def render_docx(markdown: str, title: str) -> bytes:
    pandoc = _pandoc_path()
    if not REFERENCE_DOCX.is_file():
        logger.error("export: reference.docx missing at %s", REFERENCE_DOCX)
        raise ExportUnavailableError("reference.docx missing")

    body = markdown_to_html(markdown, title)
    args = [
        pandoc,
        "--from=html",
        "--to=docx",
        # No file or network access while rendering: the input is user text.
        "--sandbox",
        f"--reference-doc={REFERENCE_DOCX}",
        "-M", "dir=rtl",
        "-M", "lang=ar",
        "--output=-",
    ]
    try:
        proc = subprocess.run(
            args,
            input=body.encode("utf-8"),
            capture_output=True,
            timeout=PANDOC_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("export: pandoc run failed: %s", exc)
        raise ExportUnavailableError("pandoc run failed") from exc
    if proc.returncode != 0 or not proc.stdout:
        logger.error(
            "export: pandoc exited %s: %s",
            proc.returncode,
            proc.stderr.decode("utf-8", "replace")[:500],
        )
        raise ExportUnavailableError("pandoc returned an error")
    try:
        return _postprocess_docx(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        logger.exception("export: docx post-process failed")
        raise ExportUnavailableError("docx post-process failed") from exc


def render(fmt: ExportFormat, markdown: str, title: str) -> bytes:
    """Blocking. Call from a worker thread."""
    if fmt == "pdf":
        return render_pdf(markdown, title)
    if fmt == "docx":
        return render_docx(markdown, title)
    raise ValueError(f"unknown export format: {fmt}")
