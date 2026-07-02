"""Blog-Post Generation API — a service-authed vertical slice.

This package exposes the SAME in-app answer-generation pipeline
(``agents.orchestrator.handle_message``) to an internal service caller
(marketing), turning a legal ``question`` into a private/unlisted
``blog_posts`` snapshot reachable at ``rayhanai.com/blog/<token>``.

It is a **transport concern** and stays under ``backend/``: the FastAPI
router, the service-key auth, the request/response models, the Arabic error
envelope, and the dedicated two-window rate limiter all live here. The
dependency direction stays one-way ``backend → agents`` — this package
*calls* the orchestrator exactly as ``message_service`` already does; the
agent it exposes does not move.

Public HTTP surface (mounted under ``/internal`` by ``backend.app.main``):

* ``POST /internal/blog-post-jobs``      — submit (service key + rate limit)
* ``GET  /internal/blog-post-jobs/{id}`` — poll (service key only)

See ``.claude/plans/blog_post_api.md`` for the full design.
"""
from __future__ import annotations

from backend.app.api.deepsearch_api import (
    auth,
    generate,
    models,
    ratelimit,
    router,
    service,
)

__all__ = ["auth", "generate", "models", "ratelimit", "router", "service"]
