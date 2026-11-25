"""Paid-only features on the HTTP side (owner 2026-11-25).

The verdict itself lives in ``shared.quota.paid_feature_access`` (one rule for
agents and routes): the writer, the sources behind an answer, and saving a
document as PDF / Word belong to the plans with a price. Free and every
marketing_* trial are refused; ``dev`` keeps them. Fails open.
"""
from __future__ import annotations

import logging

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services.case_service import get_user_id
from shared.db.run import run_db
from shared.quota import PAID_FEATURE_MESSAGE_AR, PaidFeature, PaidFeatureAccess, paid_feature_access

logger = logging.getLogger(__name__)


async def access_for_auth_id(supabase: SupabaseClient, auth_id: str) -> PaidFeatureAccess:
    """The caller's access, resolved from their auth id. Never raises."""
    try:
        user_id = await run_db(get_user_id, supabase, auth_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("paid-feature check: user lookup failed — open: %s", exc)
        return PaidFeatureAccess(allowed=True, plan_id=None)
    return await paid_feature_access(supabase, user_id)


async def require_paid_feature(
    supabase: SupabaseClient, auth_id: str, feature: PaidFeature
) -> None:
    """Raise a 403 (Arabic) when the caller's plan does not carry ``feature``."""
    access = await access_for_auth_id(supabase, auth_id)
    if not access.allowed:
        raise LunaHTTPException(
            status_code=403,
            code=ErrorCode.PAID_PLAN_REQUIRED,
            detail=PAID_FEATURE_MESSAGE_AR[feature],
        )
