"""
GET /api/v1/app/config — native app version + store config (mobile/04 §U1, B1).

Public (no auth): the app asks this on launch and on every return to the
foreground, BEFORE it has (or after it lost) a session, to decide between the
blocking «حدّث التطبيق» screen, a dismissible "update available" banner, or
nothing. The update copy itself is a fixed Arabic string inside the app.

Values come from env vars (shared/config.py APP_* fields), not a DB row: a
forced update is always paired with a backend deploy, and env vars avoid a
migration, RLS and a public anon read path.

Exempt from the 426 gate (middleware/app_version.py) — an outdated app must be
able to read the store link. Falls under the default 60/min/IP rate limit.
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel

from shared.config import get_settings

router = APIRouter()

APP_CONFIG_CACHE_CONTROL = "public, max-age=300"


class PlatformValues(BaseModel):
    ios: str
    android: str


class AppConfigResponse(BaseModel):
    min_supported_version: PlatformValues
    latest_version: PlatformValues
    store_urls: PlatformValues


@router.get("/app/config", response_model=AppConfigResponse)
async def get_app_config(response: Response) -> AppConfigResponse:
    settings = get_settings()
    response.headers["Cache-Control"] = APP_CONFIG_CACHE_CONTROL
    return AppConfigResponse(
        min_supported_version=PlatformValues(
            ios=settings.APP_MIN_VERSION_IOS,
            android=settings.APP_MIN_VERSION_ANDROID,
        ),
        latest_version=PlatformValues(
            ios=settings.APP_LATEST_VERSION_IOS,
            android=settings.APP_LATEST_VERSION_ANDROID,
        ),
        store_urls=PlatformValues(
            ios=settings.APP_STORE_URL_IOS,
            android=settings.APP_STORE_URL_ANDROID,
        ),
    )
