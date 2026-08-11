"""Receipt emails — plain «إيصال دفع», sent via Gmail SMTP on paid/refund.

Transport is the existing Google Workspace for rayhanai.com (MX
smtp.google.com; Google DKIM/SPF already in the zone — verified 2026-08-04).
No third-party email API: sending AS support@rayhanai.com rides the domain's
established reputation, and a customer's reply lands in a real inbox.

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
* **Fail-open when unconfigured:** RECEIPTS_SMTP_PASSWORD unset → log a
  warning and return. Payments must not depend on the email transport.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from base64 import b64encode
from datetime import datetime, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Any, Optional

from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.run import run_db
from shared.observability import record_smtp_probe, smtp_probe_result

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 15.0

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
      <!-- NO refund/fee copy here (owner, 2026-08-04): refund terms appear
           only at the refund action itself, inside the app. -->
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


# ───────────────────────────── smtp transport (single source) ────────────
#
# One connection recipe, split into stage-sized helpers. The real sender and
# the boot probe further down BOTH go through these, so they cannot drift
# apart on host / port / user / password / TLS. The split into three calls
# (rather than one `connect_and_login()`) is what lets the probe name WHICH
# stage failed — and that name is the entire diagnostic.


def _smtp_open(timeout: float = _SEND_TIMEOUT_S) -> smtplib.SMTP:
    """Stage ``connect`` — TCP + SMTP greeting. No TLS and no credentials yet."""
    settings = get_settings()
    return smtplib.SMTP(
        settings.RECEIPTS_SMTP_HOST, settings.RECEIPTS_SMTP_PORT, timeout=timeout
    )


def _smtp_starttls(smtp: smtplib.SMTP) -> None:
    """Stage ``starttls`` — upgrade the plaintext session to TLS."""
    smtp.starttls()


def _smtp_login(smtp: smtplib.SMTP) -> None:
    """Stage ``login`` — authenticate with the Google App Password."""
    settings = get_settings()
    smtp.login(settings.RECEIPTS_SMTP_USER, settings.RECEIPTS_SMTP_PASSWORD or "")


# ───────────────────────────── sending ───────────────────────────────────


def _smtp_send_sync(to: str, subject: str, html: str) -> None:
    """Blocking SMTP send — always called through ``run_db`` (thread offload).

    STARTTLS on 587 against Google Workspace, authenticated with an App
    Password. The message is multipart-ish minimal: HTML body with a plain
    UTF-8 fallback line (some clients preview the text part).
    """
    settings = get_settings()
    local, _, domain = settings.RECEIPTS_FROM_EMAIL.partition("@")

    msg = EmailMessage()
    msg["From"] = Address(display_name=_FROM_DISPLAY, username=local, domain=domain)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("إيصال من ريحان — افتح الرسالة بعارض HTML.")
    msg.add_alternative(html, subtype="html")

    with _smtp_open() as smtp:
        _smtp_starttls(smtp)
        _smtp_login(smtp)
        smtp.send_message(msg)


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
        if not (get_settings().RECEIPTS_SMTP_PASSWORD or "").strip():
            logger.warning(
                "receipt email skipped (%s, payment=%s): RECEIPTS_SMTP_PASSWORD unset",
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

        await run_db(_smtp_send_sync, recipient["email"], subject, html)
        logger.info("receipt email sent (%s): payment=%s no=%s", kind, payment_id, receipt_no)
    except Exception:
        # By design: a receipt failure must never surface into a payment path.
        logger.exception("receipt email failed (%s, payment=%s)", kind, payment_id)


def render_renewal_failed_notice(
    *,
    customer_name: str,
    plan_name_ar: str,
    amount_sar: Any,
    expires_at: Optional[datetime],
    final: bool,
) -> tuple[str, str]:
    """→ ``(subject, html)`` for a declined auto-renewal (dunning, plan §8).

    NOT a receipt: no money moved, so there is no receipt number and no amount
    "paid" — the amount appears only as what the renewal WOULD have cost.

    Two shapes, one template. The first-failure copy says we will try again; the
    final one says we will not, and that the subscription ends. Both point at
    the same place — إعدادات الحساب, where the stored card lives — because a
    dunning email whose link goes nowhere is theatre (plan §8).
    """
    settings = get_settings()
    # ⚠ There is no deep link to the account-settings dialog today: it is a
    # Radix dialog opened from the app chrome, and `frontend/app/settings/` is
    # an empty directory. So the CTA lands on the app and the copy tells the
    # user where to go. When a `?settings=account` (or a real /settings page)
    # lands, point this at it — a dunning email whose link needs instructions is
    # a worse version of the same email.
    manage_url = f"{settings.PUBLIC_WEB_URL}/chat"

    if final:
        subject = "انتهى اشتراكك في ريحان — تعذّر تجديد الدفع"
        headline = "تعذّر تجديد اشتراكك"
        body = (
            "حاولنا تجديد اشتراكك أكثر من مرة ولم يتم قبول عملية الدفع من البنك، "
            "لذلك لن نحاول مرة أخرى. سينتهي اشتراكك في نهاية المدة المدفوعة "
            "وسيعود حسابك إلى الباقة المجانية — بياناتك ومحادثاتك تبقى كما هي."
        )
        cta = "فتح ريحان"
    else:
        subject = "تعذّر تجديد اشتراكك في ريحان"
        headline = "تعذّر تجديد اشتراكك"
        body = (
            "حاولنا تجديد اشتراكك ولم يتم قبول عملية الدفع من البنك. "
            "سنحاول تلقائياً مرة أخرى خلال الأيام القادمة، ويستمر اشتراكك "
            "حتى نهاية المدة المدفوعة. لتفادي انقطاع الخدمة يمكنك تحديث "
            "بطاقتك الآن."
        )
        cta = "فتح ريحان"

    rows = _row("الباقة", plan_name_ar) + _row(
        "قيمة التجديد", f"{_ar_amount(amount_sar)} ريال سعودي"
    )
    if expires_at is not None:
        rows += _row("اشتراكك فعّال حتى", _ar_date(expires_at))

    inner = (
        f'<div style="font-size:15px;line-height:2;color:#2C2A28;padding-bottom:14px;">{body}</div>'
        + rows
        + f'<div style="padding-top:18px;">'
        f'<a href="{manage_url}" style="display:inline-block;background:#3D5A4D;'
        f'color:#FFFFFF;text-decoration:none;padding:11px 20px;border-radius:10px;'
        f'font-size:14px;font-weight:700;">{cta}</a></div>'
        '<div style="font-size:12px;color:#8A8378;padding-top:14px;line-height:1.9;">'
        "لتحديث بطاقتك أو إيقاف التجديد التلقائي: افتح ريحان ← إعدادات الحساب."
        "</div>"
    )
    return subject, _shell(headline, inner)


async def send_renewal_failed_notice(
    supabase: SupabaseClient,
    *,
    payment_row: dict,
    plan_name_ar: str,
    expires_at: Any = None,
    final: bool = False,
) -> None:
    """Dunning email for a declined renewal. Never raises.

    ⚠ **THE TRANSPORT IS CURRENTLY BLOCKED.** The receipt SMTP path is parked on
    the 465/SSL issue (see the boot probe below and
    `.claude/plans/moyasar_payments.md`), so in practice this logs and returns.
    That is deliberate: the plan says to WIRE the call rather than invent a
    second transport, so when receipts are unblocked dunning emails start
    flowing with no further work. Every skip is logged at WARNING with the
    payment id, because a silent renewal failure with no customer contact is the
    complaint that becomes a chargeback.

    No at-most-once claim column, unlike the receipts above: each dunning email
    belongs to ONE ``payment_transactions`` row that was just marked failed by
    the caller, and that transition happens exactly once per row. Claiming on
    ``receipt_sent_at`` was rejected — a late ``payment_paid`` webhook can still
    flip a failed row to paid, and it would then find the receipt stamp already
    burned and send no receipt for a real charge.
    """
    payment_id = payment_row.get("payment_id")
    try:
        if not (get_settings().RECEIPTS_SMTP_PASSWORD or "").strip():
            logger.warning(
                "RENEWAL DUNNING EMAIL NOT SENT (payment=%s, final=%s): "
                "RECEIPTS_SMTP_PASSWORD unset — the customer has NOT been told "
                "their renewal failed. Unblock the receipt transport.",
                payment_id, final,
            )
            return

        user_id = payment_row.get("user_id")
        recipient = await run_db(_fetch_recipient, supabase, user_id) if user_id else None
        email = (recipient or {}).get("email") or payment_row.get("customer_email_snapshot")
        if not email:
            logger.warning(
                "renewal dunning email skipped (payment=%s): no recipient email",
                payment_id,
            )
            return

        name = (
            ((recipient or {}).get("full_name_ar") or "").strip()
            or (payment_row.get("customer_name_snapshot") or "").strip()
            or "عميل ريحان"
        )
        expiry_dt = expires_at
        if isinstance(expiry_dt, str):
            try:
                expiry_dt = datetime.fromisoformat(expiry_dt.replace("Z", "+00:00"))
            except ValueError:
                expiry_dt = None

        subject, html = render_renewal_failed_notice(
            customer_name=name,
            plan_name_ar=plan_name_ar,
            amount_sar=payment_row.get("amount_sar"),
            expires_at=expiry_dt,
            final=final,
        )
        await run_db(_smtp_send_sync, email, subject, html)
        logger.info(
            "renewal dunning email sent: payment=%s final=%s", payment_id, final
        )
    except Exception:
        logger.exception(
            "renewal dunning email failed (payment=%s, final=%s) — the customer "
            "has NOT been told their renewal failed",
            payment_id, final,
        )


async def send_payment_receipt(supabase: SupabaseClient, payment_row: dict) -> None:
    """Purchase receipt — call after the grant succeeds. Never raises."""
    await _send(supabase, payment_row, stamp_column="receipt_sent_at", kind="payment")


async def send_refund_receipt(supabase: SupabaseClient, payment_row: dict) -> None:
    """Refund receipt — call after the refund is applied. Never raises."""
    await _send(supabase, payment_row, stamp_column="refund_receipt_sent_at", kind="refund")


# ═════════════════════════ boot-time transport probe ═════════════════════
#
# WHY THIS EXISTS. Every send above swallows its exception by design, so a
# transport that never delivers is indistinguishable from one that works: the
# DB stamps `receipt_sent_at`, the inbox stays empty, nothing says why. This
# probe is the missing signal. It authenticates and hangs up — it SENDS NO MAIL.
#
# HOW TO READ IT. The `stage` field is the whole diagnostic:
#   stage == "connect"  → the TCP connect never completed. Outbound 587 is
#                         blocked by the host; the fix is port 465 + implicit
#                         SSL. (No credential conclusion can be drawn — we
#                         never got far enough to present one.)
#   stage == "login"    → we reached Gmail and it refused the credentials
#                         (typically 535 5.7.8). The App Password belongs to a
#                         different Google account than RECEIPTS_SMTP_USER.
#   stage == "done"     → transport is fine; look downstream instead.
#
# RUNS ONCE PER PROCESS, from the lifespan startup task in backend/app/main.py.
# It must NEVER be driven per-request: /api/v1/_meta/observability is public and
# unauthenticated, so a probe on that path would let anonymous callers pump
# repeated failed auth attempts at our Google account (lockout risk + outbound
# amplification). The endpoint only ever READS the cached result.

_PROBE_TIMEOUT_S = 10.0          # hard wall-clock ceiling on the whole probe
_PROBE_SOCKET_TIMEOUT_S = 9.0    # per-socket; fires first so the stage is named
_PROBE_DETAIL_MAX = 200          # server text is truncated to this many chars

# Once-per-process guard. Flipped synchronously at the top of
# run_smtp_probe_once(), BEFORE its first await — on a single-threaded event
# loop that makes it impossible for a second caller to slip past it.
_probe_started = False


def _safe_detail(text: Any) -> str:
    """Server text → ≤200 chars with any credential echo scrubbed.

    Gmail's rejections do not echo credentials back, but this string is the one
    probe value that leaves the process — so it is scrubbed unconditionally
    rather than on trust, and truncated whether or not it looks long.
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    out = str(text)
    try:
        pw = (get_settings().RECEIPTS_SMTP_PASSWORD or "").strip()
    except Exception:  # noqa: BLE001 — never let scrubbing raise
        pw = ""
    if pw:
        # Plain, de-spaced (Google shows app passwords in 4-char groups), and
        # the base64 form SMTP AUTH would put on the wire.
        for form in (pw, pw.replace(" ", ""), b64encode(pw.encode()).decode()):
            if form:
                out = out.replace(form, "[redacted]")
    return out[:_PROBE_DETAIL_MAX]


def _probe_smtp_sync(progress: dict) -> dict:
    """Connect → STARTTLS → LOGIN → QUIT. Sends no mail. Never raises.

    Blocking (smtplib) — always called through ``run_db`` so the event loop
    keeps serving the healthcheck while this runs.

    ``progress['stage']`` is advanced as it goes so the async caller can still
    name the stage when the outer timeout fires while this thread is stuck (a
    thread cannot be killed). One key, one writer, one reader — dict item
    assignment is atomic under the GIL.
    """
    smtp = None
    try:
        progress["stage"] = "connect"
        smtp = _smtp_open(timeout=_PROBE_SOCKET_TIMEOUT_S)
        progress["stage"] = "starttls"
        _smtp_starttls(smtp)
        progress["stage"] = "login"
        _smtp_login(smtp)
        progress["stage"] = "done"
        return {
            "attempted": True, "ok": True, "stage": "done",
            "error_class": None, "smtp_code": None, "detail": None,
        }
    except Exception as exc:  # noqa: BLE001 — the outcome IS the return value
        code = getattr(exc, "smtp_code", None)          # SMTPResponseException
        raw = getattr(exc, "smtp_error", None)          # server's own bytes
        return {
            "attempted": True,
            "ok": False,
            "stage": progress.get("stage", "connect"),
            "error_class": type(exc).__name__,
            "smtp_code": code if isinstance(code, int) else None,
            "detail": _safe_detail(raw if raw else exc),
        }
    finally:
        # Hang up politely. On a half-open or already-dead socket quit() itself
        # raises, which must not overwrite the outcome we just built.
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                pass


async def run_smtp_probe_once() -> dict:
    """One-shot receipt-transport diagnostic. Never raises, never blocks boot.

    Fire-and-forget from the lifespan in ``backend/app/main.py``. The result is
    published to ``shared.observability`` and surfaces at
    ``/api/v1/_meta/observability`` under ``integrations.receipts_smtp.probe``.

    Second and later calls are no-ops that return the cached result — see
    ``_probe_started`` and the amplification note above.
    """
    global _probe_started
    if _probe_started:
        return smtp_probe_result()
    _probe_started = True  # BEFORE the first await — see the note above

    try:
        # Unset password ⇒ do not attempt anything. An anonymous LOGIN would be
        # a pointless failed auth against the Google account.
        if not (get_settings().RECEIPTS_SMTP_PASSWORD or "").strip():
            result = {"attempted": False, "reason": "password_unset"}
            record_smtp_probe(result)
            logger.info("SMTP probe skipped: RECEIPTS_SMTP_PASSWORD unset")
            return result

        progress: dict[str, Any] = {"stage": "connect"}
        try:
            result = await asyncio.wait_for(
                run_db(_probe_smtp_sync, progress), _PROBE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            # wait_for cannot kill the worker thread; the socket timeout below
            # it retires that thread shortly after. What matters is that the
            # probe RESOLVES here, carrying the stage the thread got stuck on —
            # a hang at "connect" is the port-blocked signature.
            result = {
                "attempted": True, "ok": False,
                "stage": progress.get("stage", "connect"),
                "error_class": "TimeoutError", "smtp_code": None,
                "detail": f"probe exceeded {_PROBE_TIMEOUT_S:g}s wall clock",
            }
    except Exception as exc:  # noqa: BLE001 — a diagnostic must not crash boot
        # Reaching here means the probe broke BEFORE any socket work (settings
        # failed to load, the thread offload could not be scheduled). Reporting
        # it as attempted/stage=connect would forge the port-blocked signature
        # and send the reader after the wrong suspect, so it gets its own shape.
        result = {
            "attempted": False, "reason": "probe_error",
            "error_class": type(exc).__name__, "detail": _safe_detail(exc),
        }

    record_smtp_probe(result)
    if result.get("ok"):
        logger.info("SMTP probe OK — receipt transport connected and authenticated")
    else:
        logger.warning(
            "SMTP probe FAILED at stage=%s (%s, code=%s): %s",
            result.get("stage"), result.get("error_class"),
            result.get("smtp_code"), result.get("detail"),
        )
    return result
