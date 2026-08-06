"""Receipt email templates — content and the NO-TAX-LANGUAGE guard.

The business holds no VAT registration (decision 2026-08-04), so receipts are
plain «إيصال دفع» and may not contain tax language of any kind. The guard
tests here are the enforcement: if someone reintroduces «ضريبة» / «فاتورة» /
a VAT split into the templates, this file fails the build.

The second half covers the boot-time SMTP probe: that it stays silent when
unconfigured, connects at most ONCE per process (the public observability
endpoint must never be able to drive Gmail auth attempts), names the failing
stage, and never echoes the App Password.
"""
from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timezone

import pytest

from backend.app.services import receipt_service as rs
from backend.app.services.receipt_service import (
    STATEMENT_AR,
    render_payment_receipt,
    render_refund_receipt,
)
from shared import observability
from shared.config import get_settings
from shared.observability import integrations_status

_DT = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

# Any of these appearing in a receipt turns it into (or implies) a tax
# document, which a non-VAT-registered business must not issue.
_FORBIDDEN = ["ضريب", "فاتورة", "VAT", "٪15", "15٪", "%15", "15%"]


def _both() -> list[tuple[str, str]]:
    return [
        render_payment_receipt(
            receipt_no=7, customer_name="مستخدم تجريبي", amount_sar="49.90", paid_at=_DT
        ),
        render_refund_receipt(
            receipt_no=7,
            customer_name="مستخدم تجريبي",
            refunded_amount_sar="47.90",
            refunded_at=_DT,
        ),
    ]


def test_no_tax_language_anywhere():
    for subject, html in _both():
        blob = subject + html
        for token in _FORBIDDEN:
            assert token not in blob, f"tax language {token!r} leaked into a receipt"


def test_payment_receipt_contents():
    subject, html = render_payment_receipt(
        receipt_no=7, customer_name="مستخدم تجريبي", amount_sar="49.90", paid_at=_DT
    )
    assert "RYH-000007" in subject
    assert "إيصال دفع" in html
    assert "مستخدم تجريبي" in html          # customer name
    assert "٤٩٫٩٠" in html                   # amount, Arabic-Indic, 2dp
    assert "٤ أغسطس ٢٠٢٦" in html            # receipt date
    assert STATEMENT_AR in html              # the بيان line
    # Owner (2026-08-04): NO refund/fee copy in the purchase receipt — the
    # policy surfaces only at the refund action inside the app.
    assert "استرداد" not in html
    assert "رسوم" not in html


def test_refund_receipt_contents():
    subject, html = render_refund_receipt(
        receipt_no=7,
        customer_name="مستخدم تجريبي",
        refunded_amount_sar="47.90",
        refunded_at=_DT,
    )
    assert "إيصال استرداد" in subject
    assert "٤٧٫٩٠" in html                   # refunded amount, not the charge
    assert "أُلغي الاشتراك" in html          # revocation is stated


def test_missing_receipt_no_renders_dash():
    _, html = render_payment_receipt(
        receipt_no=None, customer_name="x y", amount_sar="49.90", paid_at=_DT
    )
    assert "RYH-" not in html
    assert "—" in html


# ═══════════════════════ boot-time SMTP probe ════════════════════════════

APP_PASSWORD = "abcd efgh ijkl mnop"   # Google App Password shape, 16 + spaces


@pytest.fixture
def probe_env(monkeypatch):
    """Virgin probe state + settings we dictate. Restored on teardown."""
    get_settings.cache_clear()
    s = get_settings()
    monkeypatch.setattr(s, "RECEIPTS_SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(s, "RECEIPTS_SMTP_PORT", 587)
    monkeypatch.setattr(s, "RECEIPTS_SMTP_USER", "noreply@rayhanai.com")
    monkeypatch.setattr(s, "RECEIPTS_SMTP_PASSWORD", APP_PASSWORD)
    # monkeypatch restores the module flag (False at import) after each test.
    monkeypatch.setattr(rs, "_probe_started", False)
    observability.record_smtp_probe({"attempted": False, "reason": "not_run"})
    yield s
    observability.record_smtp_probe({"attempted": False, "reason": "not_run"})
    get_settings.cache_clear()


def _fake_smtp(monkeypatch, *, fail_at: str | None = None, exc: Exception | None = None):
    """Patch smtplib.SMTP; return the list of connection ATTEMPTS it recorded.

    Patched at the stdlib class, not at ``_smtp_open``, so the test also proves
    the probe dials the host/port/timeout the settings actually carry.
    """
    conns: list[dict] = []

    class _Fake:
        def __init__(self, host, port, timeout=None):
            conns.append({"host": host, "port": port, "timeout": timeout})
            self.steps: list = []
            if fail_at == "connect":
                raise exc  # type: ignore[misc]

        def starttls(self):
            self.steps.append("starttls")
            if fail_at == "starttls":
                raise exc  # type: ignore[misc]

        def login(self, user, password):
            self.steps.append(("login", user, password))
            if fail_at == "login":
                raise exc  # type: ignore[misc]

        def quit(self):
            self.steps.append("quit")

    monkeypatch.setattr(smtplib, "SMTP", _Fake)
    return conns


def test_probe_skipped_when_password_unset(probe_env, monkeypatch):
    """Unconfigured ⇒ report and stop. An anonymous LOGIN is never attempted."""
    monkeypatch.setattr(probe_env, "RECEIPTS_SMTP_PASSWORD", None)
    conns = _fake_smtp(monkeypatch)

    result = asyncio.run(rs.run_smtp_probe_once())

    assert result == {"attempted": False, "reason": "password_unset"}
    assert conns == []
    assert integrations_status()["receipts_smtp"]["probe"]["attempted"] is False


def test_probe_connects_once_per_process(probe_env, monkeypatch):
    """The once-per-process guard: call it twice, Gmail sees one connection."""
    conns = _fake_smtp(monkeypatch)

    first = asyncio.run(rs.run_smtp_probe_once())
    second = asyncio.run(rs.run_smtp_probe_once())

    assert first["ok"] is True and first["stage"] == "done"
    assert second == first          # cached, not re-probed
    assert len(conns) == 1
    assert conns[0]["host"] == "smtp.example.test"
    assert conns[0]["port"] == 587
    assert conns[0]["timeout"] == rs._PROBE_SOCKET_TIMEOUT_S


def test_observability_reads_never_reprobe(probe_env, monkeypatch):
    """/api/v1/_meta/observability is PUBLIC — reading it must never dial out.

    A per-request probe would let anonymous callers pump failed auth attempts
    at the Google account. The endpoint only reads the cached result.
    """
    conns = _fake_smtp(monkeypatch)
    asyncio.run(rs.run_smtp_probe_once())

    for _ in range(5):
        snapshot = integrations_status()

    assert len(conns) == 1
    assert snapshot["receipts_smtp"]["probe"]["ok"] is True


def test_probe_names_connect_stage_when_port_is_blocked(probe_env, monkeypatch):
    """Suspect 2 signature: nothing answers on 587 ⇒ stage 'connect'."""
    _fake_smtp(monkeypatch, fail_at="connect", exc=TimeoutError("timed out"))

    result = asyncio.run(rs.run_smtp_probe_once())

    assert result["attempted"] is True and result["ok"] is False
    assert result["stage"] == "connect"      # NOT 'login' — no credential verdict
    assert result["error_class"] == "TimeoutError"
    assert result["smtp_code"] is None


def test_probe_names_login_stage_with_smtp_code(probe_env, monkeypatch):
    """Suspect 1 signature: Gmail answered and refused ⇒ stage 'login' + 535."""
    _fake_smtp(
        monkeypatch,
        fail_at="login",
        exc=smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted."
        ),
    )

    result = asyncio.run(rs.run_smtp_probe_once())

    assert result["stage"] == "login"        # we REACHED Gmail; port is open
    assert result["smtp_code"] == 535
    assert result["error_class"] == "SMTPAuthenticationError"
    assert "Username and Password not accepted" in result["detail"]


def test_probe_detail_is_truncated_and_scrubs_the_password(probe_env, monkeypatch):
    """The one probe value that leaves the process: ≤200 chars, no credential."""
    leak = APP_PASSWORD.replace(" ", "").encode()
    _fake_smtp(
        monkeypatch,
        fail_at="login",
        exc=smtplib.SMTPAuthenticationError(535, b"5.7.8 rejected " + leak + b" x" * 400),
    )

    detail = asyncio.run(rs.run_smtp_probe_once())["detail"]

    assert len(detail) <= rs._PROBE_DETAIL_MAX == 200
    assert APP_PASSWORD not in detail
    assert APP_PASSWORD.replace(" ", "") not in detail
    assert "[redacted]" in detail


def test_probe_error_is_not_disguised_as_a_blocked_port(probe_env, monkeypatch):
    """A probe that broke before any socket work must not forge stage='connect'.

    'connect' is the port-blocked verdict; claiming it for a settings failure
    would point the reader at the wrong suspect.
    """
    def _boom():
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(rs, "get_settings", _boom)

    result = asyncio.run(rs.run_smtp_probe_once())

    assert result["attempted"] is False
    assert result["reason"] == "probe_error"
    assert "stage" not in result
    assert result["error_class"] == "RuntimeError"


def test_probe_authenticates_but_sends_no_mail(probe_env, monkeypatch):
    """Connect → STARTTLS → LOGIN → QUIT, with the configured user. No send."""
    sessions: list = []

    class _Recording:
        def __init__(self, host, port, timeout=None):
            self.steps: list = []
            sessions.append(self)

        def starttls(self):
            self.steps.append("starttls")

        def login(self, user, password):
            self.steps.append(f"login:{user}")

        def quit(self):
            self.steps.append("quit")

    monkeypatch.setattr(smtplib, "SMTP", _Recording)
    asyncio.run(rs.run_smtp_probe_once())

    assert len(sessions) == 1
    assert sessions[0].steps == ["starttls", "login:noreply@rayhanai.com", "quit"]
    assert not hasattr(sessions[0], "sent")   # no send_message / sendmail path
