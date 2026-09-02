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
        description=(
            "Optional title. When null the aggregator's first-line H1 headline is "
            "lifted out of the body (public_blog_service.extract_headline). A "
            "supplied title WINS and the H1 line is stripped either way."
        ),
    )
    display_mode: str = Field(
        default="question",
        description=(
            "VESTIGIAL for this path. public_blogs has no display_mode column — "
            "the public wing is always the article template (blog_subjects §6). "
            "Kept (and still validated) for the blog_posts path only."
        ),
    )

    # ── the public_blogs row (blog_subjects.md D16, marketing_agents.md §3) ──
    # ``type`` is deliberately Optional[str] rather than a required Literal: this
    # module's validation policy (see the header) keeps enum-ish fields as plain
    # ``str`` so ``router._validate_request`` can answer an **Arabic 400** instead
    # of FastAPI's non-Arabic 422. Absent and out-of-vocabulary are both 400s.
    type: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED. public_blogs.type — laws_explanation | judicial_research | "
            "compliance. Carried by the BLOG, never by a subject (D3). Rendered as "
            "a badge; never a URL."
        ),
    )
    subjects: list[str] = Field(
        default_factory=list,
        description=(
            "blog_subjects.slug values (ASCII kebab-case) this blog is filed under. "
            "An unknown slug is a 400, NEVER a silent drop — a blog with no subject "
            "is invisible in the browse tree and nobody notices until the traffic "
            "does not arrive (blog_subjects §5)."
        ),
    )
    slug: Optional[str] = Field(
        default=None,
        description=(
            "The blog's permanent Arabic address. Minted from the resolved title "
            "when null. Refused at mint time when it is reserved ('subjects'), "
            "collides with a subject slug, or is ASCII kebab-shaped (D4/§3)."
        ),
    )
    publish_public: bool = Field(
        default=True,
        description=(
            "Sets public_blogs.is_public — LISTING only (gallery + sitemap). "
            "false still lands the row and the slug still resolves (unlisted, not "
            "hidden). Genuinely unreachable is is_published=false, which "
            "publish_policy/min_confidence owns: a 'low'-confidence article is "
            "written unpublished regardless of what this asked for (§5). "
            "⚠ Defaults TRUE, matching D17 and public_blogs.is_public's own "
            "column default: writing a row into a table called public_blogs IS "
            "creating a public blog. A false default would make an omitted flag "
            "produce an article absent from the browse tree and the sitemap — "
            "the failure nobody notices until the traffic does not arrive."
        ),
    )
    editorial_voice: bool = Field(
        default=True,
        description=(
            "Select the editorial aggregator prompt twin (blog_subjects §6) — the "
            "article voice for a stranger arriving from a search engine, rather "
            "than the in-app answer to a lawyer who asked and is waiting."
        ),
    )

    # ── retrieval pinning — BOTH OPTIONAL (blog_subjects.md §5, D9) ─────────
    mode: Optional[str] = Field(
        default=None,
        description=(
            "case_led | reg_compliance_led | full — or null. ⚠ null is VALID and "
            "means 'the planner decides'; it must never be coerced to a default. "
            "Both mode AND support set ⇒ phase 1 is skipped entirely; either "
            "absent ⇒ phase 1 runs and whatever was supplied is overlaid on its "
            "output."
        ),
    )
    support: Optional[bool] = Field(
        default=None,
        description=(
            "Run the mode's support executor — true | false | null. "
            "⚠ MUST stay Optional[bool]. A plain ``bool = False`` cannot express "
            "'not pinned': an absent support would be indistinguishable from a "
            "deliberate support=false, and every partially-pinned job would "
            "silently lose its support executor — no error, no log line, just a "
            "thinner article (blog_subjects §5 + §11)."
        ),
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
    """The completed-job result payload (matches LUNA_API_REQUEST §6).

    ⚠ **Every key marketing reads must be DECLARED here.** ``result`` is stored
    on the job row as raw JSON and re-parsed into this model on the poll path,
    so an undeclared key is silently stripped on the way out — the failure that
    looks exactly like success (``regulation_appendix_surface``).
    """

    # ``post_id`` is the uuid of the ``public_blogs`` VERSION this job wrote
    # (v1's ``blog_id``). Since blog_subjects D16 the editorial job no longer
    # creates a ``blog_posts`` row at all, so there is no ``blog_posts.post_id``
    # to report and ``token`` is always null (D17 — a public blog is open; the
    # slug is the whole address, there is no unguessable string to hold).
    #
    # ⚠ **STABLE ACROSS RE-DRIVES.** One job publishes at most one blog, held by
    # a unique index on ``public_blogs.job_id`` (migration 156), so polling the
    # same job after a backend restart returns the SAME ``post_id`` / ``root_id``
    # / ``slug`` it returned before. Before that index a re-drive published a
    # SECOND article under a freshly-generated headline — the slug could not
    # dedupe because it comes from a non-deterministic LLM output. An SEO
    # rewrite is the one thing that moves ``post_id`` (it appends a new version);
    # ``root_id`` and ``slug`` survive that too, which is why every later
    # marketing call addresses ``root_id``.
    post_id: Optional[str] = None
    token: Optional[str] = None
    # The LOGICAL blog. Every later marketing call (/seo, /cards, /retract)
    # addresses this, not a version — an SEO rewrite changes ``post_id`` and
    # leaves ``root_id`` and ``slug`` untouched (marketing_agents §3).
    root_id: Optional[str] = None
    slug: Optional[str] = None
    # LISTED in the gallery + sitemap. Distinct from ``is_published``: an
    # is_public=false blog still resolves at its slug, unlisted (§5's table).
    is_public: bool = False
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


class PublicBlogRetractResponse(BaseModel):
    """``POST /internal/public-blogs/{root_id}/retract`` (blog_subjects D11).

    Retract DELISTS only — ``deleted_at`` and ``is_published`` are untouched, so
    the URL keeps resolving for anyone holding the link. Because a delisted page
    is still a live 200 it does **not** deindex; ``robots: noindex`` on the
    frontend (driven by this very flag) is what does.
    """

    root_id: str
    is_public: bool = False


__all__ = [
    "BlogPostJobRequest",
    "BlogJobConfidence",
    "BlogJobReferenceItem",
    "BlogJobReferences",
    "BlogJobResult",
    "BlogJobError",
    "BlogJobSubmitResponse",
    "BlogJobStatusResponse",
    "PublicBlogRetractResponse",
]
