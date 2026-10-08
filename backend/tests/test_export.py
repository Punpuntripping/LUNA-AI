"""POST /api/v1/export — PDF / Word download (document_export_pdf_word.md).

No network, no DB. A minimal app mounts only the export router (plus the Luna
exception handler) so the tests do not depend on Redis/Supabase wiring; one
test checks the real ``backend.app.main`` app registers the route.

The PDF render test runs only where WeasyPrint's native libs load (CI image /
Docker); everywhere else the 503 path is exercised through a forced import
failure, which is what a box without pango would hit.
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import export as export_api
from backend.app.deps import get_current_user
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.services import export_service
from shared.auth.jwt import AuthUser

TITLE = "مذكرة دفاع في الدعوى رقم 123"

SAMPLE_MD = """## الوقائع

تقدّم المدعي بدعواه استناداً إلى **المادة 76** من *نظام المعاملات المدنية* [1]، وأرفق «مرفق رقم 1».

| م | البيان | المرجع |
|---|---|---|
| 1 | عقد الإيجار المؤرخ | خلية-الجدول-الفريدة |

1. الحكم بفسخ العقد.
2. إلزام المدعى عليه بالأجرة.

المراجع
1-نظام المعاملات المدنية
2-434939 — 1443 — وزارة العدل
"""


def _weasyprint_loadable() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def _pandoc_available() -> bool:
    try:
        import pypandoc

        pypandoc.get_pandoc_path()
    except Exception:  # noqa: BLE001
        return False
    return True


def _pdf_base_fonts(pdf: bytes) -> set[bytes]:
    """``/BaseFont`` names, including those inside compressed object streams."""
    import zlib

    chunks = [pdf]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            chunks.append(zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    names: set[bytes] = set()
    for chunk in chunks:
        names |= set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-]+)", chunk))
    return names


def _fake_user() -> AuthUser:
    return AuthUser(
        auth_id="11111111-1111-1111-1111-111111111111",
        email="t@example.com",
        role="authenticated",
    )


@pytest.fixture(autouse=True)
def _reset_limiter():
    export_api.export_rate_limit._fallback.reset()
    yield
    export_api.export_rate_limit._fallback.reset()


def _app(authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(export_api.router, prefix="/api/v1")
    if authed:
        app.dependency_overrides[get_current_user] = _fake_user
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def _post(client: TestClient, **body):
    payload = {"format": "docx", "title": TITLE, "markdown": SAMPLE_MD}
    payload.update(body)
    return client.post("/api/v1/export", json=payload)


def _detail(resp) -> str:
    return resp.json()["detail"]


def _is_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text))


# ---------------------------------------------------------------------------
# Wiring / auth
# ---------------------------------------------------------------------------


def test_main_app_registers_export_route_without_loading_renderers():
    """Fresh interpreter: importing the app registers the route and loads
    neither renderer (lazy imports — a box without pango must still boot)."""
    import subprocess

    code = (
        "import sys, backend.app.main as m;"
        "paths={getattr(r,'path',None) for r in m.app.routes};"
        "assert '/api/v1/export' in paths, 'route missing';"
        "assert 'weasyprint' not in sys.modules, 'weasyprint imported at boot';"
        "assert 'pypandoc' not in sys.modules, 'pypandoc imported at boot';"
        "print('ok')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ok")


def test_auth_required():
    resp = TestClient(_app(authed=False)).post(
        "/api/v1/export", json={"format": "pdf", "title": "x", "markdown": "y"}
    )
    assert resp.status_code == 401
    assert _is_arabic(_detail(resp))


# ---------------------------------------------------------------------------
# Validation — 422 / 413
# ---------------------------------------------------------------------------


def test_422_bad_format(client):
    resp = _post(client, format="odt")
    assert resp.status_code == 422
    assert _detail(resp) == export_api.MSG_EXPORT_FORMAT


def test_422_title_too_long(client):
    resp = _post(client, title="ع" * (export_service.MAX_TITLE_CHARS + 1))
    assert resp.status_code == 422
    assert _detail(resp) == export_api.MSG_EXPORT_TITLE


def test_422_empty_markdown(client):
    resp = _post(client, markdown="   \n ")
    assert resp.status_code == 422
    assert _detail(resp) == export_api.MSG_EXPORT_EMPTY


def test_422_missing_markdown_and_bad_json(client):
    resp = client.post("/api/v1/export", json={"format": "pdf"})
    assert resp.status_code == 422 and _is_arabic(_detail(resp))
    resp = client.post(
        "/api/v1/export", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422 and _is_arabic(_detail(resp))
    resp = client.post("/api/v1/export", json=["pdf"])
    assert resp.status_code == 422


def test_413_markdown_over_cap(client):
    resp = _post(client, markdown="ا" * (export_service.MAX_MARKDOWN_CHARS + 1))
    assert resp.status_code == 413
    assert _detail(resp) == export_api.MSG_EXPORT_TOO_LARGE


def test_413_declared_body_over_cap(client):
    resp = client.post(
        "/api/v1/export",
        content=b"x" * (export_api.MAX_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


def test_rate_limited_after_budget(client, monkeypatch):
    monkeypatch.setattr(
        export_service, "render", lambda fmt, md, title: b"%PDF-fake"
    )
    for _ in range(export_api.EXPORT_RATE_LIMIT):
        assert _post(client, format="pdf").status_code == 200
    resp = _post(client, format="pdf")
    assert resp.status_code == 429
    assert _is_arabic(_detail(resp))


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pandoc_available(), reason="pypandoc_binary not installed")
def test_docx_round_trip(client):
    resp = _post(client, format="docx")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == export_service.MEDIA_TYPES["docx"]

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert zf.testzip() is None
    doc = zf.read("word/document.xml").decode("utf-8")
    assert TITLE in doc                       # title promoted to H1
    assert "الوقائع" in doc                    # heading text
    assert "خلية-الجدول-الفريدة" in doc        # table cell text
    assert "«مرفق رقم 1»" in doc
    assert "[1]" in doc
    assert "<w:bidi" in doc                    # RTL paragraphs
    assert "<w:bidiVisual" in doc              # RTL table column order
    assert "<w:tblHeader" in doc               # header row repeats
    assert 'w:val="Heading1"' in doc
    assert "المراجع" in doc and "434939" in doc
    # A4 + RTL section + the «صفحة n من m» footer come from reference.docx.
    assert '<w:pgSz w:h="16838" w:w="11906"' in doc or 'w:w="11906"' in doc
    footer = zf.read("word/footer1.xml").decode("utf-8")
    assert "PAGE" in footer and "NUMPAGES" in footer and "صفحة" in footer


@pytest.mark.skipif(not _pandoc_available(), reason="pypandoc_binary not installed")
def test_docx_title_not_duplicated_when_body_opens_with_h1():
    data = export_service.render_docx("# عنوان المستند\n\nنص.", "عنوان آخر")
    doc = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
    assert "عنوان المستند" in doc
    assert "عنوان آخر" not in doc


def test_docx_503_when_pandoc_missing(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypandoc", None)  # import → ImportError
    resp = _post(client, format="docx")
    assert resp.status_code == 503
    assert _detail(resp) == "تعذّر إنشاء الملف حالياً، حاول مرة أخرى"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _weasyprint_loadable(), reason="WeasyPrint native libs absent")
def test_pdf_renders(client):
    resp = _post(client, format="pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    fonts = _pdf_base_fonts(resp.content)
    # The committed Arabic face is embedded (regular + bold) …
    assert any(b"IBM-Plex-Sans-Arabic" in f and not f.endswith(b"-Bold") for f in fonts), fonts
    assert any(f.endswith(b"IBM-Plex-Sans-Arabic-Bold") for f in fonts), fonts
    # … and no proportional fallback face was needed for the text.
    assert not any(f.endswith(b"+DejaVu-Sans") for f in fonts), fonts


@pytest.mark.skipif(not _weasyprint_loadable(), reason="WeasyPrint native libs absent")
def test_pdf_refuses_external_resources():
    html = export_service.build_pdf_html("x", "t")
    assert "file://" in html  # fonts only
    seen: list[str] = []
    import weasyprint

    real_fetch = weasyprint.URLFetcher.fetch

    def spy(self, url, headers=None):
        seen.append(url)
        return real_fetch(self, url, headers)

    weasyprint.URLFetcher.fetch = spy
    try:
        export_service.render_pdf('[a](http://169.254.169.254/) ![i](http://169.254.169.254/x.png)', "t")
    finally:
        weasyprint.URLFetcher.fetch = real_fetch
    assert all(u.startswith("file:") for u in seen)


def test_pdf_503_when_weasyprint_import_fails(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # import → ImportError
    resp = _post(client, format="pdf")
    assert resp.status_code == 503
    assert _detail(resp) == "تعذّر إنشاء الملف حالياً، حاول مرة أخرى"


def test_pdf_503_when_native_libs_missing(client, monkeypatch):
    """What a box without pango actually raises: OSError at import time."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "weasyprint", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    resp = _post(client, format="pdf")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Content-Disposition / filename
# ---------------------------------------------------------------------------


def test_content_disposition_utf8_arabic_filename(client, monkeypatch):
    monkeypatch.setattr(export_service, "render", lambda fmt, md, title: b"%PDF-fake")
    resp = _post(client, format="pdf", title="مذكرة/دفاع: «نهائية»?")
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert cd.startswith('attachment; filename="document.pdf"; ')
    m = re.search(r"filename\*=UTF-8''(\S+)$", cd)
    assert m, cd
    assert unquote(m.group(1)) == "مذكرة دفاع «نهائية».pdf"
    assert resp.headers["cache-control"] == "private, no-store"


def test_sanitize_filename_stem():
    s = export_service.sanitize_filename_stem
    assert s("") == "document"
    assert s("  ../..  ") == "document"
    assert s('a\\b/c:d*e?f"g<h>i|j') == "a b c d e f g h i j"
    assert s("ملف‮gpj.exe") == "ملفgpj.exe"  # bidi override stripped
    assert len(s("ع" * 500)) == export_service.MAX_FILENAME_STEM_CHARS


# ---------------------------------------------------------------------------
# Markdown → HTML (shared by both formats)
# ---------------------------------------------------------------------------


def test_markdown_to_html_structure_and_safety():
    md = (
        "نص\n\n<img src=\"file:///etc/passwd\">\n\n![بديل](http://169.254.169.254/x)\n\n"
        "| أ | ب |\n|---|---|\n| 1 | 2 |\n\n~~محذوف~~\n\nالمراجع\n1-أ\n2-ب\n"
    )
    out = export_service.markdown_to_html(md, "عنوان <b>")
    assert out.startswith("<h1>عنوان &lt;b&gt;</h1>")
    assert "<img" not in out                       # raw HTML escaped, images → alt
    assert "بديل" in out
    assert "<thead>" in out and "<td>1</td>" in out
    assert "<s>محذوف</s>" in out
    assert "<h2>المراجع</h2>" in out
    assert "1-أ<br />" in out                       # reference lines stay separate


def test_title_h1_skipped_when_body_starts_with_h1():
    out = export_service.markdown_to_html("# أصل\n\nنص", "عنوان")
    assert "عنوان" not in out
    out = export_service.markdown_to_html("## فرعي\n\nنص", "عنوان")
    assert out.startswith("<h1>عنوان</h1>")
