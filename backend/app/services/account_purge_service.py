"""Daily hard-purge sweep for accounts past their deletion grace period.

A user who requests deletion gets ``users.deletion_requested_at`` stamped and is
locked out of every data route immediately (grace-period gate in
``case_service.get_user_id``). Once that stamp is older than
``GRACE_PERIOD_DAYS`` the account is unrecoverable and this sweep erases it:
storage objects → child tables (transactional RPC) → the GoTrue user.

Triggered once a day by the APScheduler job registered in ``backend.app.main``'s
lifespan (03:45 UTC + a one-shot startup catch-up). Sync — callers run it via
``asyncio.to_thread``. Per-user isolation: one bad account never stops the sweep.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from supabase import Client as SupabaseClient

from backend.app.services.account_service import GRACE_PERIOD_DAYS
from shared.config import get_settings
from shared.storage.client import delete_folder_recursive

logger = logging.getLogger(__name__)


def _purge_one(supabase: SupabaseClient, user_id: str, auth_id: str) -> None:
    """Erase one account: storage → child rows → auth user. Raises on failure.

    ORDERING IS LOAD-BEARING. The ``users`` row is this sweep's selection marker
    and dies ONLY in step (d) — in the same DB transaction as the auth.users row
    it cascades from. Steps (a)–(c) are idempotent, so any partial failure leaves
    ``deletion_requested_at`` intact and tomorrow's sweep retries the whole user.
    No "auth user with no users row" straggler state is reachable, and no extra
    tracking table is needed.
    """
    bucket = get_settings().STORAGE_BUCKET_DOCUMENTS

    # (a) Capture case_ids BEFORE anything is deleted — the RPC in (c) removes
    #     lawyer_cases, after which the `cases/{case_id}` prefixes are
    #     unreachable. No deleted_at filter: soft-deleted cases still own files.
    result = (
        supabase.table("lawyer_cases")
        .select("case_id")
        .eq("lawyer_user_id", user_id)
        .execute()
    )
    case_ids = [row["case_id"] for row in (result.data or [])]

    # (b) Storage first. delete_folder_recursive RAISES, so a storage failure
    #     aborts before any DB row is touched — never orphan files.
    files = delete_folder_recursive(bucket, f"general/{user_id}", supabase=supabase)
    for case_id in case_ids:
        files += delete_folder_recursive(bucket, f"cases/{case_id}", supabase=supabase)

    # (c) Child tables — one transaction (service-role only RPC, migration 090).
    supabase.rpc("purge_user_data", {"p_user_id": user_id}).execute()

    # (d) Terminal step: deleting the GoTrue user cascades
    #     users.auth_id -> auth.users(id), which removes the now-slim users row.
    supabase.auth.admin.delete_user(auth_id)

    logger.info(
        "account purge: purged user_id=%s auth_id=%s (%d cases, %d files)",
        user_id,
        auth_id,
        len(case_ids),
        files,
    )

    # (e) Audit — DIRECT insert, not write_audit_log(): that helper always sets
    #     user_id, whose FK row no longer exists (the column is the ON DELETE
    #     SET NULL target and is nullable). Failure here must not fail the
    #     purge — it already happened and is not re-runnable.
    try:
        supabase.table("audit_logs").insert(
            {
                "action": "delete",
                "resource_type": "account",
                "resource_id": str(user_id),
                "metadata": {"event": "purged", "auth_id": str(auth_id)},
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "account purge: audit insert failed for user_id=%s: %s", user_id, exc
        )


def purge_expired_accounts(supabase: SupabaseClient) -> dict[str, int]:
    """Hard-delete accounts whose grace period has expired.

    Returns ``{scanned, purged, failed}``. Never raises — a scheduler tick must
    not be able to crash the app.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=GRACE_PERIOD_DAYS)
    stats = {"scanned": 0, "purged": 0, "failed": 0}

    try:
        result = (
            supabase.table("users")
            .select("user_id, auth_id")
            .lte("deletion_requested_at", cutoff.isoformat())  # partial index
            .execute()
        )
        rows = result.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("account purge: query failed: %s", exc)
        return stats

    stats["scanned"] = len(rows)
    if not rows:
        logger.info("account purge: no accounts past the %d-day grace period",
                    GRACE_PERIOD_DAYS)
        return stats

    for row in rows:
        user_id = row.get("user_id")
        auth_id = row.get("auth_id")
        try:
            _purge_one(supabase, user_id, auth_id)
            stats["purged"] += 1
        except Exception as exc:  # noqa: BLE001
            # One bad account must never stop the sweep; the users row survives,
            # so the next run retries this user from the top.
            stats["failed"] += 1
            logger.exception(
                "account purge: failed for user_id=%s: %s", user_id, exc
            )

    logger.info(
        "account purge: scanned=%d purged=%d failed=%d",
        stats["scanned"],
        stats["purged"],
        stats["failed"],
    )
    return stats


if __name__ == "__main__":
    # Manual run for testing:
    #   python -m backend.app.services.account_purge_service
    logging.basicConfig(level=logging.INFO)
    from shared.db.client import get_supabase_client

    print(purge_expired_accounts(get_supabase_client()))


__all__ = ["purge_expired_accounts"]
