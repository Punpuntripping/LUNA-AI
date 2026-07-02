"""Pydantic request + response models for the Blog-Post Generation API.

Contract mirrors ``.claude/plans/LUNA_API_REQUEST.md`` §5 (request body) and
§6 (poll response). Field names are load-bearing — marketing reads them by
name — so do NOT rename without coordinating.

Validation policy: the request model keeps enum-ish fields as plain ``str``
with defaults (rather than ``Literal``) so the router can validate them and
return an **Arabic 400** envelope (``LunaHTTPException``) instead of FastAPI's
default non-Arabic 422. Only ``question`` and ``idempotency_key`` are hard
required (a totally-absent field still surfaces as 422, acceptable for an
internal service caller); their non-emptiness + the enum values are checked in
``router._validate_request``.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class BlogPostJobRequest(BaseModel):
    """The body marketing POSTs to ``/internal/blog-post-jobs``.

    ``idempotency_key`` + ``question`` are required; everything else defaults.
    ``metadata`` is opaque provenance passthrough (stored on the job row, never
    surfaced in the public response — ``question_raw`` must not leak).
    """

    idempotency_key: str = Field(
        ...,
        description="Stable dedup key. Same key returns the existing job — never a second post.",
    )
    question: str = Field(
        ...,
        description="Anonymized, self-contained legal question. Becomes question_text + drives generation.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Optional title; the engine's artifact title is used when null.",
    )
    display_mode: str = Field(
        default="question",
        description="blog_posts.display_mode — 'question' (default) or 'title'.",
    )
    subtype: str = Field(
        default="marketing_telegram",
        description="Tag stored on blog_posts.subtype so marketing posts are filterable.",
    )
    language: str = Field(default="ar", description="Answer language (informational).")
    publish_policy: str = Field(
        default="auto",
        description="auto | always | never. auto => publish iff confidence >= min_confidence.",
    )
    min_confidence: str = Field(
        default="medium",
        description="high | medium | low — the threshold the 'auto' policy compares against. "
        "Default 'medium' => medium and high confidence publish; only 'low' stays an unpublished draft.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form provenance echoed on the job row. Never surfaced publicly.",
    )
    callback_url: Optional[str] = Field(
        default=None,
        description="Optional URL best-effort POSTed with the result on completion.",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class BlogJobConfidence(BaseModel):
    """Confidence signal marketing gates outreach on."""

    label: str = Field(description="high | medium | low.")
    score: Optional[float] = Field(default=None, description="Optional 0–1 derived score.")
    reasons: list[str] = Field(default_factory=list, description="Human-readable Arabic rationale.")


class BlogJobReferenceItem(BaseModel):
    """One entry in ``references.top`` — a cited source preview."""

    n: int = Field(description="1-based citation number used inline as [n].")
    title: str = Field(default="", description="Human-readable source title (Arabic).")
    relevance: Optional[str] = Field(default=None, description="Reranker tag: high | medium.")


class BlogJobReferences(BaseModel):
    """Cited-reference summary for the result payload."""

    count: int = Field(default=0, description="Total cited references.")
    top: list[BlogJobReferenceItem] = Field(
        default_factory=list, description="First few cited references (preview)."
    )


class BlogJobResult(BaseModel):
    """The completed-job result payload (matches LUNA_API_REQUEST §6)."""

    post_id: Optional[str] = None
    token: Optional[str] = None
    url: Optional[str] = None
    is_published: bool = False
    confidence: BlogJobConfidence
    title: Optional[str] = None
    question_text: str = ""
    summary: str = ""
    content_md: str = ""
    references: BlogJobReferences = Field(default_factory=BlogJobReferences)
    workspace_item_id: Optional[str] = None
    created_at: Optional[str] = None


class BlogJobError(BaseModel):
    """The failed-job error object."""

    code: str
    message: str
    retryable: bool = False


class BlogJobSubmitResponse(BaseModel):
    """202 (new) / 200 (idempotency replay) submit response."""

    job_id: str
    status: str
    status_url: str


class BlogJobStatusResponse(BaseModel):
    """GET poll response. ``result`` on completed, ``error`` on failed."""

    job_id: str
    status: str
    result: Optional[BlogJobResult] = None
    error: Optional[BlogJobError] = None


__all__ = [
    "BlogPostJobRequest",
    "BlogJobConfidence",
    "BlogJobReferenceItem",
    "BlogJobReferences",
    "BlogJobResult",
    "BlogJobError",
    "BlogJobSubmitResponse",
    "BlogJobStatusResponse",
]
