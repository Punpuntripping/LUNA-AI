"""Usage limits API — /api/v1/usage.

Read-only snapshot of the four bars rendered by the Settings → Usage limits
dialog. Backed by shared.quota.current_usage_report; Redis is the hot path
with PG rehydration on miss.
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
          "ord": {"daily":   {"used", "limit", "pct", "resets_at"},
                  "weekly":  {...}},
          "ocr": {"monthly": {...}},
          "web": {"monthly": {...}}
        }
    """
    user_id = get_user_id(supabase, current_user.auth_id)
    return await quota.current_usage_report(redis, supabase, user_id)
