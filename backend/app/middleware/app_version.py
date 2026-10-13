"""
Native-app minimum-version gate — 426 APP_UPDATE_REQUIRED (mobile/04 §U2, B2).

Touches a request ONLY when BOTH hold:

* ``X-App-Platform`` is ``ios`` or ``android`` (case-insensitive), and
* ``X-App-Version`` parses as a version AND is strictly below that platform's
  minimum (``APP_MIN_VERSION_IOS`` / ``APP_MIN_VERSION_ANDROID``).

Everything else passes through untouched — in particular the web, which never
sends ``X-App-Platform``. A garbage version (or a garbage configured minimum)
never blocks: a parse failure must not lock a paying user out of the app.

The comparison is on the NATIVE version (``nativeApplicationVersion``). An OTA
update does not change it, which is the point: the gate exists for changes a
JS-only update cannot deliver.

Exempt so an outdated app can still learn it is outdated, reach the store link
and sign out: ``/api/v1/app/config``, ``/api/v1/health``, ``/api/v1/auth/logout``.

Pure ASGI (not ``BaseHTTPMiddleware``) so pass-through requests — SSE streams
included — are forwarded with zero buffering.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.app.errors import ErrorCode, MSG_APP_UPDATE_REQUIRED
from shared.config import get_settings

logger = logging.getLogger(__name__)

APP_UPDATE_STATUS = 426

EXEMPT_PATHS = frozenset(
    {
        "/api/v1/app/config",
        "/api/v1/health",
        "/api/v1/auth/logout",
    }
)

NATIVE_PLATFORMS = frozenset({"ios", "android"})

# Bounded so a hostile header cannot make the regex do real work.
_MAX_VERSION_LEN = 32

# "1", "1.2", "1.2.3", optional leading "v", optional "-pre"/"+build" suffix.
_VERSION_RE = re.compile(r"v?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:[-+][0-9A-Za-z.\-+]*)?")


def parse_version(raw: Optional[str]) -> Optional[tuple[int, int, int]]:
    """Parse a semver-ish string into a comparable (major, minor, patch).

    Missing minor/patch count as 0. Returns None for anything else — callers
    treat None as "unknown, do not block".
    """
    if not raw:
        return None
    value = raw.strip()
    if not value or len(value) > _MAX_VERSION_LEN:
        return None
    m = _VERSION_RE.fullmatch(value)
    if m is None:
        return None
    major, minor, patch = m.groups()
    return int(major), int(minor or 0), int(patch or 0)


def min_version_for(platform: str) -> Optional[str]:
    settings = get_settings()
    if platform == "ios":
        return settings.APP_MIN_VERSION_IOS
    if platform == "android":
        return settings.APP_MIN_VERSION_ANDROID
    return None


def update_required(platform_header: Optional[str], version_header: Optional[str]) -> bool:
    """The whole gate decision, header values in → block or not."""
    if not platform_header:
        return False
    platform = platform_header.strip().lower()
    if platform not in NATIVE_PLATFORMS:
        return False
    current = parse_version(version_header)
    if current is None:
        return False
    minimum = parse_version(min_version_for(platform))
    if minimum is None:
        logger.warning(
            "app_version: unparsable minimum for %s — gate disabled", platform
        )
        return False
    return current < minimum


def app_update_required_response() -> JSONResponse:
    """Same nested envelope as ``errors.luna_exception_handler``."""
    return JSONResponse(
        status_code=APP_UPDATE_STATUS,
        content={
            "error": {
                "code": ErrorCode.APP_UPDATE_REQUIRED.value,
                "message": MSG_APP_UPDATE_REQUIRED,
                "status": APP_UPDATE_STATUS,
            },
            "detail": MSG_APP_UPDATE_REQUIRED,
        },
        headers={"Cache-Control": "no-store"},
    )


def _header(scope: Scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            try:
                return value.decode("latin-1")
            except Exception:  # noqa: BLE001
                return None
    return None


class AppVersionGateMiddleware:
    """ASGI middleware answering 426 to native clients below the minimum."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        platform = _header(scope, b"x-app-platform")
        if platform is None or scope.get("path", "") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        try:
            blocked = update_required(platform, _header(scope, b"x-app-version"))
        except Exception as e:  # noqa: BLE001 — never block on a gate bug
            logger.warning("app_version gate error (failing open): %s", e)
            blocked = False

        if not blocked:
            await self.app(scope, receive, send)
            return

        logger.info(
            "app_version.update_required platform=%s version=%s path=%s",
            platform.strip().lower(),
            (_header(scope, b"x-app-version") or "")[:_MAX_VERSION_LEN],
            scope.get("path", ""),
        )
        await app_update_required_response()(scope, receive, send)
