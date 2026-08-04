"""Receipt email templates — content and the NO-TAX-LANGUAGE guard.

The business holds no VAT registration (decision 2026-08-04), so receipts are
plain «إيصال دفع» and may not contain tax language of any kind. The guard
tests here are the enforcement: if someone reintroduces «ضريبة» / «فاتورة» /
a VAT split into the templates, this file fails the build.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.services.receipt_service import (
    STATEMENT_AR,
    render_payment_receipt,
    render_refund_receipt,
)

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
    assert "رسوم معالجة ٢ ريال" in html      # refund clause in the footer


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
