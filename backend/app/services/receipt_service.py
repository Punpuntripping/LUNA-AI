"""Receipt emails — plain «إيصال دفع», sent via Resend on paid/refund.

NOT a tax invoice, deliberately (decision 2026-08-04): the business holds no
VAT registration, so the receipt carries NO tax language — no فاتورة ضريبية,
no VAT split, no percentages. The ``vat_amount_sar``/``net_amount_sar``
columns from migration 113 remain internal bookkeeping and appear nowhere in
the email. If a VAT number is obtained later, this same template gains the
number + a ZATCA QR (TLV) and becomes a simplified tax invoice; receipt
numbering (migration 114 sequence) is already continuous for that future.

Content, per the owner's spec: receipt date · customer name · TOTAL amount
only (the actually-charged amount — prorated on upgrades) · the بيان line ·
Rayhan branding. Nothing else.

Delivery rules:
* **Never raises into a payment path.** Every public function swallows every
  exception after logging — a lost email is support-recoverable, a failed
  payment is not.
* **Sends at most once** per payment per kind, via an atomic claim: the sent
  stamp (``receipt_sent_at`` / ``refund_receipt_sent_at``) is written with a
  conditional UPDATE first; only the caller that wins the claim sends. The
  verify path and the webhook path can race freely.
* **Fail-open when unconfigured:** RESEND_API_KEY unset → log a warning and
  return. Payments must not depend on the email vendor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_SEND_TIMEOUT_S = 8.0

# Display name on the From header. The address itself comes from settings.
_FROM_DISPLAY = "ريحان"

# The بيان line, verbatim per the owner's spec (2026-08-04). One constant —
# change it here, nowhere else.
STATEMENT_AR = "مقابل اشتراك في تطبيق ريحان للذكاء الاصطناعي"

# ───────────────────────────── formatting ────────────────────────────────

_ARABIC_DIGITS = str.maketrans("0123456789.", "٠١٢٣٤٥٦٧٨٩٫")

_ARABIC_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]


def _ar_amount(value: Any) -> str:
    """``49.90`` / ``'49.90'`` → ``'٤٩٫٩٠'`` — two decimals, Arabic-Indic."""
    return f"{float(value):.2f}".translate(_ARABIC_DIGITS)


def _ar_date(dt: datetime) -> str:
    """``2026-08-04`` → ``'٤ أغسطس ٢٠٢٦'``."""
    day = str(dt.day).translate(_ARABIC_DIGITS)
    year = str(dt.year).translate(_ARABIC_DIGITS)
    return f"{day} {_ARABIC_MONTHS[dt.month - 1]} {year}"


def _receipt_no_display(receipt_no: Optional[int]) -> str:
    """``7`` → ``'RYH-000007'`` (Latin — it is an identifier, not prose)."""
    return f"RYH-{int(receipt_no):06d}" if receipt_no else "—"


# ───────────────────────────── templates ─────────────────────────────────
#
# Email-client constraints, not app CSS: inline styles only, no external
# fonts (Noto Naskh Arabic falls back gracefully), table-free simple divs,
# brand palette hand-copied from globals.css (warm light theme: primary
# green #3D5A4D, warm border #E0D6CA).


def _shell(title: str, inner: str) -> str:
    return f"""\
<div dir="rtl" lang="ar" style="margin:0;padding:32px 16px;background:#F7F4EF;font-family:'Noto Naskh Arabic','Segoe UI',Tahoma,Arial,sans-serif;color:#2C2A28;">
  <div style="max-width:520px;margin:0 auto;">
    <div style="text-align:center;padding-bottom:20px;">
      <div style="font-size:26px;font-weight:700;color:#3D5A4D;">ريحان</div>
      <div style="font-size:13px;color:#8A8378;margin-top:2px;">المساعد القانوني الذكي</div>
    </div>
    <div style="background:#FFFFFF;border:1px solid #E0D6CA;border-radius:14px;padding:28px;">
      <div style="font-size:18px;font-weight:700;color:#2C2A28;padding-bottom:16px;border-bottom:1px solid #E0D6CA;margin-bottom:16px;">{title}</div>
      {inner}
    </div>
    <div style="text-align:center;font-size:12px;color:#8A8378;padding-top:20px;line-height:1.9;">
      يمكنك طلب استرداد المبلغ خلال ٢٤ ساعة من الشراء · رسوم معالجة ٢ ريال<br/>
      <a href="https://rayhanai.com" style="color:#3D5A4D;text-decoration:none;">rayhanai.com</a>
      &nbsp;·&nbsp;
      <a href="mailto:support@rayhanai.com" style="color:#3D5A4D;text-decoration:none;" dir="ltr">support@rayhanai.com</a>
    </div>
  </div>
</div>"""


def _row(label: str, value: str, *, strong: bool = False) -> str:
    weight = "700" if strong else "400"
    size = "17px" if strong else "14px"
    return (
        f'<div style="display:flex;justify-content:space-between;gap:12px;'
        f'padding:7px 0;font-size:{size};">'
        f'<span style="color:#8A8378;">{label}</span>'
        f'<span style="font-weight:{weight};color:#2C2A28;">{value}</span></div>'
    )


def render_payment_receipt(
    *,
    receipt_no: Optional[int],
    customer_name: str,
    amount_sar: Any,
    paid_at: datetime,
) -> tuple[str, str]:
    """→ ``(subject, html)`` for the purchase receipt. NO tax language."""
    subject = f"إيصال دفع من ريحان — {_receipt_no_display(receipt_no)}"
    inner = (
        _row("رقم الإيصال", _receipt_no_display(receipt_no))
        + _row("تاريخ الإيصال", _ar_date(paid_at))
        + _row("اسم العميل", customer_name)
        + _row("البيان", STATEMENT_AR)
        + _row("المبلغ الإجمالي", f"{_ar_amount(amount_sar)} ريال سعودي", strong=True)
    )
    return subject, _shell("إيصال دفع", inner)


def render_refund_receipt(
    *,
    receipt_no: Optional[int],
    customer_name: str,
    refunded_amount_sar: Any,
    refunded_at: datetime,
) -> tuple[str, str]:
    """→ ``(subject, html)`` for the refund receipt. NO tax language."""
    subject = f"إيصال استرداد من ريحان — {_receipt_no_display(receipt_no)}"
    inner = (
        _row("رقم الإيصال الأصلي", _receipt_no_display(receipt_no))
        + _row("تاريخ الاسترداد", _ar_date(refunded_at))
        + _row("اسم العميل", customer_name)
        + _row("البيان", f"استرداد — {STATEMENT_AR}")
        + _row(
            "المبلغ المسترد",
            f"{_ar_amount(refunded_amount_sar)} ريال سعودي",
            strong=True,
        )
        + '<div style="font-size:12px;color:#8A8378;padding-top:10px;">'
        "أُلغي الاشتراك المرتبط بهذه العملية. يصل المبلغ خلال أيام العمل "
        "المعتادة حسب البنك المُصدر.</div>"
    )
    return subject, _shell("إيصال استرداد", inner)


# ───────────────────────────── db helpers (sync, via run_db) ─────────────


def _claim_send(supabase: SupabaseClient, payment_id: str, stamp_column: str) -> bool:
    """Atomically claim the right to send. True = we own the send."""
    res = (
        supabase.table("payment_transactions")
        .update({stamp_column: datetime.now(timezone.utc).isoformat()})
        .eq("payment_id", payment_id)
        .is_(stamp_column, "null")
        .execute()
    )
    return bool(getattr(res, "data", None))


def _fetch_recipient(supabase: SupabaseClient, user_id: str) -> Optional[dict]:
    res = (
        supabase.table("users")
        .select("email, full_name_ar")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _fetch_receipt_no(supabase: SupabaseClient, payment_id: str) -> Optional[int]:
    res = (
        supabase.table("payment_transactions")
        .select("receipt_no")
        .eq("payment_id", payment_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0].get("receipt_no") if rows else None


# ───────────────────────────── sending ───────────────────────────────────


async def _post_resend(to: str, subject: str, html: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_SEND_TIMEOUT_S) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={
                "from": f"{_FROM_DISPLAY} <{settings.RECEIPTS_FROM_EMAIL}>",
                "to": [to],
                "subject": subject,
                "html": html,
            },
        )
        resp.raise_for_status()


async def _send(
    supabase: SupabaseClient,
    payment_row: dict,
    *,
    stamp_column: str,
    kind: str,
) -> None:
    """Shared claim → render → send skeleton. Swallows everything."""
    payment_id = payment_row.get("payment_id")
    try:
        if not (get_settings().RESEND_API_KEY or "").strip():
            logger.warning(
                "receipt email skipped (%s, payment=%s): RESEND_API_KEY unset",
                kind, payment_id,
            )
            return

        claimed = await run_db(_claim_send, supabase, payment_id, stamp_column)
        if not claimed:
            return  # the other paid-path already sent it

        recipient = await run_db(_fetch_recipient, supabase, payment_row["user_id"])
        if not recipient or not recipient.get("email"):
            logger.warning(
                "receipt email skipped (%s, payment=%s): no recipient email",
                kind, payment_id,
            )
            return

        # receipt_no is trigger-assigned during the paid UPDATE — the row dict
        # the caller holds may predate it, so read it back.
        receipt_no = await run_db(_fetch_receipt_no, supabase, payment_id)
        name = (recipient.get("full_name_ar") or "").strip() or "عميل ريحان"
        now = datetime.now(timezone.utc)

        if kind == "refund":
            subject, html = render_refund_receipt(
                receipt_no=receipt_no,
                customer_name=name,
                refunded_amount_sar=payment_row.get("refunded_amount_sar"),
                refunded_at=now,
            )
        else:
            subject, html = render_payment_receipt(
                receipt_no=receipt_no,
                customer_name=name,
                amount_sar=payment_row.get("amount_sar"),
                paid_at=now,
            )

        await _post_resend(recipient["email"], subject, html)
        logger.info("receipt email sent (%s): payment=%s no=%s", kind, payment_id, receipt_no)
    except Exception:
        # By design: a receipt failure must never surface into a payment path.
        logger.exception("receipt email failed (%s, payment=%s)", kind, payment_id)


async def send_payment_receipt(supabase: SupabaseClient, payment_row: dict) -> None:
    """Purchase receipt — call after the grant succeeds. Never raises."""
    await _send(supabase, payment_row, stamp_column="receipt_sent_at", kind="payment")


async def send_refund_receipt(supabase: SupabaseClient, payment_row: dict) -> None:
    """Refund receipt — call after the refund is applied. Never raises."""
    await _send(supabase, payment_row, stamp_column="refund_receipt_sent_at", kind="refund")
