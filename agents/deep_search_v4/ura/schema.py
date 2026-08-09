"""Unified Retrieval Artifact (URA) schema -- v3.0.

URA is the canonical merged retrieval object that flows from the domain
executors (reg_search — now spanning regulations, appendixes, circulars and
services via the unified ``search_topics`` corpus — and case_search) into the
aggregator. A single ``UnifiedRetrievalArtifact`` carries:

- ``high_results`` / ``medium_results`` -- relevance-tiered buckets.
- Per-domain result classes -- ``RegURAResult``, ``ComplianceURAResult``,
  ``CircularURAResult``, ``CaseURAResult`` -- wired through a Pydantic
  discriminated union on the ``domain`` field.

v3.0 reshape (URA Two-View Reframe):
- Each result class holds the **full unfolded data** for its kept result.
- Two typed projections per result -- ``.for_aggregator()`` (synthesis input)
  and ``.for_reference()`` (citation metadata). The generic ``title`` /
  ``content`` fields are gone from the base; each domain names its own.
- The heavy fields (full content, cross-refs, landing urls, entity names) are
  filled post-merge by ``ura/enrich.py`` -- the adapters build lightweight
  shells, ``enrich_ura`` mutates them in place.

Mutation contract (load-bearing): these models MUST stay plain ``BaseModel`` --
no ``frozen=True``, no ``validate_assignment=True``. ``ura/enrich.py`` mutates
result instances in place after the merger builds them.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Domain = Literal["regulations", "compliance", "circulars", "cases"]


# -- Cross-ref caps -- applied at projection time, not at fetch time ----------
# enrich.py fetches + dedups every cross-ref; the projections truncate.
MAX_CROSS_REFS_AGG_REG = 5     # reg aggregator view
MAX_CROSS_REFS_AGG_CASE = 3    # case aggregator view (referenced_regulations)
MAX_CROSS_REFS_REF = 10        # both domains, reference view


# -- Circular content cap (D11) ----------------------------------------------
# Aggregator view = full circular content capped at 4,000 chars (p90=3,958;
# max outlier 168,782) with an Arabic truncation marker. The cap is applied at
# adapter time so the persisted URA never carries the 168k outlier; the user
# view (uncapped full content) is rebuilt from the DB downstream (Wave 3).
MAX_CIRCULAR_CONTENT_AGG = 4_000
CIRCULAR_TRUNCATION_MARKER = "… [اقتُطع النص]"


def cap_circular_content(
    text: str | None, limit: int = MAX_CIRCULAR_CONTENT_AGG
) -> str:
    """Return circular content capped at ``limit`` chars with the D11 marker.

    Text at or under the cap is returned trimmed; longer text is cut at the cap
    and suffixed with :data:`CIRCULAR_TRUNCATION_MARKER`.
    """
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit].rstrip() + " " + CIRCULAR_TRUNCATION_MARKER


# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------


class CrossRef(BaseModel):
    """One resolved cross-reference from a chunk (or case) to a target unit.

    ``target_type`` is an open ``str`` (NOT a ``Literal``) on purpose -- a new
    type (``appendix``) is being added and persisted artifacts must reload
    without a validation error. Renders as
    ``{target_reg_title}, {target_type}:{target_number}`` + ``content``.
    """

    target_type: str = ""
    target_reg_title: str = ""
    target_number: int | None = None
    relation: str = ""
    content: str = ""  # resolved body; "" when unresolved / null target_id


# Parity with the PUBLIC article page's free window
# (``library_service.ARTICLE_FREE_CHARS``). Deliberately duplicated rather than
# imported: agents/ must not depend on backend/.
CROSS_REF_REFERENCE_FREE_CHARS = 500


# Resolver-telemetry keys the case pipeline wrote into every
# ``referenced_regulations`` entry — internal corpus/chunk ids and match
# scores, dropped from any client-bound (reference-view) projection.
_CASE_REF_INTERNAL_KEYS = frozenset(
    {
        "regulation_id",
        "target_chunk_ids",
        "confidence",
        "bm25_score",
        "vector_score",
        "combined_score",
        "match_method",
    }
)


def _cut_cross_ref_body(body: str) -> str:
    """Cut one body to the free window on a word boundary."""
    if len(body) <= CROSS_REF_REFERENCE_FREE_CHARS:
        return body
    cut = body[:CROSS_REF_REFERENCE_FREE_CHARS]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + " …"


def gate_cross_refs_for_reference(refs: list) -> list:
    """Truncate cross-referenced مادة bodies on the way into a REFERENCE view.

    The access-tiers work moved full source bodies behind a metered reveal, but
    ``cross_refs[].content`` carries the resolved body of each cross-referenced
    مادة -- up to ``MAX_CROSS_REFS_REF`` of them per reference, shipped free in
    the citation-list payload. Measured on live panels that was 21.7% of the
    remaining payload (one panel 64.7%), i.e. a free side-channel to exactly the
    مواد bodies an ``article`` unlock costs 1 to open.

    The plan (§6.2) says keep ``cross_refs``, and §1.3 puts *citation lists (the
    mesh)* in the never-gated class -- but it puts *regulation article bodies* in
    the PARTIALLY GATED class (§1.4). Both hold: the mesh survives intact
    (target_reg_title, target_type, target_number, relation), while the body is
    cut to the same window the public article page already shows anonymously.
    Chat therefore leaks nothing a logged-out visitor cannot already read, and
    the panel keeps the context that makes a cross-reference useful.

    ``for_aggregator`` is deliberately NOT gated -- the model needs the full text
    to reason, and that payload never reaches the user.

    Handles BOTH shapes that feed ``ReferenceView``: the regulation domain passes
    ``CrossRef`` models, while the case domain's ``referenced_regulations`` is a
    ``list[dict]``. Both land in the same panel, so both must be gated.

    Case dicts additionally get sanitised: the pipeline's resolver wrote its
    telemetry into each entry (``regulation_id``, ``target_chunk_ids``,
    ``confidence``, bm25/vector/combined scores, ``match_method``) — internal
    ids that must not ship in a client payload — and stores its body under
    ``reference_content``, which the free-window cut must cover too.
    """
    out: list = []
    for cr in refs:
        if isinstance(cr, dict):
            cleaned = {
                k: v for k, v in cr.items() if k not in _CASE_REF_INTERNAL_KEYS
            }
            for body_key in ("content", "reference_content"):
                body = str(cleaned.get(body_key) or "")
                if len(body) > CROSS_REF_REFERENCE_FREE_CHARS:
                    cleaned[body_key] = _cut_cross_ref_body(body)
            out.append(cleaned)
            continue

        body = getattr(cr, "content", "") or ""
        if len(body) <= CROSS_REF_REFERENCE_FREE_CHARS:
            out.append(cr)
        else:
            out.append(cr.model_copy(update={"content": _cut_cross_ref_body(body)}))
    return out


# ---------------------------------------------------------------------------
# Projection target models
# ---------------------------------------------------------------------------


class AggregatorItem(BaseModel):
    """Trimmed projection a URA result exposes to the aggregator prompt builder.

    Flat and ``domain``-tagged -- each domain fills only its own subset of
    fields. The prompt builder switches on ``domain``.

    ``n`` is the **shared citation index** -- the same 1-based number the
    preprocessor stamps on the matching ``Reference``. ``for_aggregator(n)``
    receives it from the preprocessor (the URA result cannot know its own tier
    position). It is what the aggregator cites inline as ``[n]``; both
    projections are keyed by this single index.
    """

    ref_id: str
    n: int = 0
    domain: Domain
    relevance: Literal["high", "medium"]
    # regulations
    reg_title: str = ""
    reg_scope: str = ""
    chunk_content: str = ""
    cross_refs: list[CrossRef] = Field(default_factory=list)
    corpus: str = ""  # "appendix" -> (ملحق) tag; "" for the main statutory body
    # compliance
    service_name: str = ""
    service_context: str = ""
    provider_name: str = ""
    # circulars
    circular_title: str = ""
    circular_content: str = ""
    entity_name: str = ""
    # cases
    case_number: str | None = None
    # DERIVED SUMMARY, not the ruling text: `cases.summary` (structured markdown
    # — ## الملخص / ## الوقائع / ## المطالبات / ## اسانيد … / ## التسبيب /
    # ## المنطوق), clipped to 6k by `case_search/unfold_ura.py`. Decision D2 in
    # `.claude/plans/case_topics_loop.md`. The full ruling text is no longer in
    # the synthesis prompt — do NOT assert findings the summary does not state.
    case_content: str = ""
    court: str = ""
    court_level: str = ""  # first_instance | appeal | supreme (informational, D5)
    referenced_regulations: list[dict] = Field(default_factory=list)


class ReferenceView(BaseModel):
    """Trimmed projection a URA result exposes to the citation builder.

    The aggregator preprocessor stamps the 1-based ``n`` and attaches the
    ``source_view`` to turn this into a final ``Reference`` -- those two are
    preprocessor concerns, not something a URA result can produce.
    """

    ref_id: str
    domain: Domain
    source_type: str
    relevance: Literal["high", "medium"]
    # regulations
    reg_title: str = ""
    landing_url: str = ""
    # regulations_v2.doc_type_raw (لائحة / تنظيم / دليل / …) — the reference
    # card's type chip. "" when the corpus has no determined type; the UI then
    # falls back to its generic نظام label.
    doc_type: str = ""
    cross_refs: list[CrossRef] = Field(default_factory=list)
    corpus: str = ""  # "appendix" -> (ملحق) tag; "" for the main statutory body
    # compliance
    service_name: str = ""
    provider_name: str = ""
    service_url: str = ""
    url: str = ""
    # circulars (entity_name is shared with cases, below)
    circular_title: str = ""
    source_url: str = ""
    # cases
    case_number: str | None = None
    judgment_number: str | None = None
    court: str | None = None
    city: str | None = None
    details_url: str | None = None
    entity_name: str = ""
    referenced_regulations: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# URA result classes
# ---------------------------------------------------------------------------


class URAResultBase(BaseModel):
    """Cross-domain plumbing shared by every URA result.

    No generic ``title`` / ``content`` -- each domain names its own content
    fields (v3.0). ``domain`` (the discriminator) lives on each subclass.
    """

    ref_id: str
    source_type: str
    relevance: Literal["high", "medium"]
    reasoning: str = ""
    appears_in_sub_queries: list[int] = Field(default_factory=list)
    rrf_max: float = 0.0


class RegURAResult(URAResultBase):
    """Regulations-domain URA result (one kept chunk).

    The adapter builds the shell (base fields only); ``ura/enrich.py`` fills
    every field below post-merge.
    """

    domain: Literal["regulations"] = "regulations"
    reg_title: str = ""
    reg_scope: str = ""
    chunk_content: str = ""
    chunk_context: str = ""        # stored only
    cross_refs: list[CrossRef] = Field(default_factory=list)
    landing_url: str = ""
    # regulations_v2.doc_type_raw — display-only (reference card type chip).
    # Deliberately NOT projected into for_aggregator(): the synthesis prompt
    # surface is unchanged, so the prompt-cache prefix stays intact.
    doc_type: str = ""
    pdf_url: str = ""              # stored only
    owns: dict = Field(default_factory=dict)  # stored only
    corpus: str = ""              # "appendix" -> (ملحق) tag (D13); "" = main body

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="regulations",
            relevance=self.relevance,
            reg_title=self.reg_title,
            reg_scope=self.reg_scope,
            chunk_content=self.chunk_content,
            cross_refs=list(self.cross_refs[:MAX_CROSS_REFS_AGG_REG]),
            corpus=self.corpus,
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="regulations",
            source_type=self.source_type,
            relevance=self.relevance,
            reg_title=self.reg_title,
            landing_url=self.landing_url,
            doc_type=self.doc_type,
            cross_refs=gate_cross_refs_for_reference(
                self.cross_refs[:MAX_CROSS_REFS_REF]
            ),
            corpus=self.corpus,
        )


class ComplianceURAResult(URAResultBase):
    """Compliance-domain URA result (one government service).

    The compliance adapter already carries every field below -- ``enrich_ura``
    is a no-op for this domain.
    """

    domain: Literal["compliance"] = "compliance"
    service_name: str = ""
    service_context: str = ""     # compact user/reference view (D10)
    provider_name: str = ""
    service_url: str = ""
    url: str = ""                 # fallback link for service_url
    service_ref: str = ""         # stored only -- mints ref_id upstream
    sectors: list[str] = Field(default_factory=list)  # stored only
    is_most_used: bool = False    # stored only
    is_proactive: bool = False    # stored only
    # Structured payload for the RICH aggregator view (D10 / §1d). Optional:
    # absent on old stored refs + references_service-rebuilt shells, in which
    # case ``for_aggregator`` falls back to ``service_context``.
    intro_title: str = ""
    intro_description: str = ""
    steps: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        # RICH aggregator view (D10): build from the structured fields, falling
        # back to the compact ``service_context`` when they are absent.
        from agents.deep_search_v4.ura.services_unfold import (
            build_service_aggregator_content,
        )

        content = build_service_aggregator_content(
            service_name=self.service_name,
            intro_title=self.intro_title,
            provider_name=self.provider_name,
            intro_description=self.intro_description,
            steps=self.steps,
            requirements=self.requirements,
            required_documents=self.required_documents,
            service_url=self.service_url,
            url=self.url,
            fallback_context=self.service_context,
        )
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="compliance",
            relevance=self.relevance,
            service_name=self.service_name,
            service_context=content,
            provider_name=self.provider_name,
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="compliance",
            source_type=self.source_type,
            relevance=self.relevance,
            service_name=self.service_name,
            provider_name=self.provider_name,
            service_url=self.service_url,
            url=self.url,
        )


class CircularURAResult(URAResultBase):
    """Circulars-domain URA result (one ministerial circular — تعميم).

    New under the unified ``search_topics`` corpus (D5/D9). Circular rows arrive
    fully hydrated from the reg_search loop, so the type-aware ``reg_adapter``
    carries every field below at adapter time — ``ura/enrich.py`` is a no-op for
    this domain (mirrors compliance).

    ``content`` holds the **aggregator view** (D11): the full circular body
    capped at :data:`MAX_CIRCULAR_CONTENT_AGG` chars with an Arabic truncation
    marker. The uncapped user view is rebuilt from the DB downstream (Wave 3).
    """

    domain: Literal["circulars"] = "circulars"
    circ_ref: str = ""
    title: str = ""
    entity_name: str = ""
    content: str = ""             # aggregator view: capped 4k + truncation marker
    source_url: str = ""          # circulars.source
    sectors: list[str] = Field(default_factory=list)  # stored only

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="circulars",
            relevance=self.relevance,
            circular_title=self.title,
            circular_content=self.content,
            entity_name=self.entity_name,
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="circulars",
            source_type=self.source_type,
            relevance=self.relevance,
            circular_title=self.title,
            entity_name=self.entity_name,
            source_url=self.source_url,
        )


class CaseURAResult(URAResultBase):
    """Cases-domain URA result (one court ruling).

    The case adapter carries case content / metadata; ``enrich_ura`` adds the
    reference-view fields the reranker output lacks (``details_url`` and the
    resolved ``entity_name``).

    ``case_content`` holds the **aggregator view** (D2): `cases.summary`
    clipped to 6k chars by ``case_search/unfold_ura.py`` — a derived structured
    summary, NOT the raw ruling text. The full ruling is rebuilt from the DB for
    the user-facing source view (``source_viewer.py``).
    """

    domain: Literal["cases"] = "cases"
    case_number: str | None = None
    case_content: str = ""        # aggregator view: cases.summary, clipped 6k
    referenced_regulations: list[dict] = Field(default_factory=list)
    judgment_number: str | None = None
    court: str | None = None
    city: str | None = None
    details_url: str | None = None
    entity_name: str = ""
    entity_id: str | None = None  # stored only -- entity_name resolve key
    title: str = ""               # stored only
    # first_instance | appeal | supreme -- surfaced in the aggregator view as
    # ``المحكمة: {court} ({level})``. Informational only (D5): no retrieval
    # boost, no reranker instruction to prefer appeal/supreme.
    court_level: str | None = None
    date_hijri: str | None = None   # stored only
    legal_domains: list[str] = Field(default_factory=list)  # stored only
    appeal_result: str | None = None  # stored only

    def for_aggregator(self, n: int = 0) -> AggregatorItem:
        return AggregatorItem(
            ref_id=self.ref_id,
            n=n,
            domain="cases",
            relevance=self.relevance,
            case_number=self.case_number,
            case_content=self.case_content,
            court=self.court or "",
            court_level=self.court_level or "",
            referenced_regulations=list(
                self.referenced_regulations[:MAX_CROSS_REFS_AGG_CASE]
            ),
        )

    def for_reference(self) -> ReferenceView:
        return ReferenceView(
            ref_id=self.ref_id,
            domain="cases",
            source_type=self.source_type,
            relevance=self.relevance,
            case_number=self.case_number,
            judgment_number=self.judgment_number,
            court=self.court,
            city=self.city,
            details_url=self.details_url,
            entity_name=self.entity_name,
            referenced_regulations=gate_cross_refs_for_reference(
                self.referenced_regulations[:MAX_CROSS_REFS_REF]
            ),
        )


URAResult = Annotated[
    Union[RegURAResult, ComplianceURAResult, CircularURAResult, CaseURAResult],
    Field(discriminator="domain"),
]


class UnifiedRetrievalArtifact(BaseModel):
    """Tiered, typed retrieval artifact consumed by the aggregator."""

    schema_version: str = "3.0"
    query_id: int = 0
    log_id: str = ""
    original_query: str = ""
    produced_at: str = ""
    produced_by: dict = Field(
        default_factory=lambda: {
            "reg_search": False,
            "compliance_search": False,
            "case_search": False,
        }
    )
    sub_queries: list[dict] = Field(default_factory=list)
    high_results: list[URAResult] = Field(default_factory=list)
    medium_results: list[URAResult] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)
    sector_filter: list[str] = Field(default_factory=list)


__all__ = [
    "Domain",
    "CrossRef",
    "AggregatorItem",
    "ReferenceView",
    "URAResultBase",
    "RegURAResult",
    "ComplianceURAResult",
    "CircularURAResult",
    "CaseURAResult",
    "URAResult",
    "UnifiedRetrievalArtifact",
    "MAX_CROSS_REFS_AGG_REG",
    "MAX_CROSS_REFS_AGG_CASE",
    "MAX_CROSS_REFS_REF",
    "CROSS_REF_REFERENCE_FREE_CHARS",
    "gate_cross_refs_for_reference",
    "MAX_CIRCULAR_CONTENT_AGG",
    "CIRCULAR_TRUNCATION_MARKER",
    "cap_circular_content",
]
