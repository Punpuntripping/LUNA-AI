"""Usage limits API — /api/v1/usage.

Read-only snapshot of the bars rendered by the Settings → Usage limits dialog
(points session/weekly, OCR pages, and — since the access-tiers work — the
library «فتح المصادر» allowance). Backed by shared.quota.current_usage_report,
which reads the get_user_quota_state RPC (migration 093, widened by 105) — the
same single source the enforcement gate and Layer B use, so the dialog always
shows exactly what is enforced.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_redis, get_supabase
from backend.app.services.case_service import get_user_id
from shared import quota
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/usage")
async def get_usage(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """Return the current usage snapshot for the authenticated user.

    Shape::

        {
          "locked": false,
          "plan":    {"plan_id", "name_ar", "expires_at", "expired", ...} | null,
          "points":  {"session": {...}, "weekly": {...}, "monthly": {...}},
          "ocr":     {"monthly": {...}},
          "web":     {"monthly": {...}},
          "library": {"period":  {...}}
        }

    Points are the user-facing spend unit (1 USD = 100 points); ``limit: null``
    = unlimited; ``locked: true`` = no plan assigned yet. The ``library`` bar
    counts weighted unlocks (SUM of ``library_unlocks.cost``) for the current
    period and — unlike the rolling points/OCR bars — always carries a
    ``resets_at``, because its window is a fixed calendar/subscription period.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    return await quota.current_usage_report(redis, supabase, user_id)
