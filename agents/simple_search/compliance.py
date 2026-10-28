"""The ``simple_search`` compliance path — service search + the compliance agent.

Plan ``.claude/plans/simple_search_compliance_path.md`` §3–§5 (decisions D1–D8).

**Why a second path at all.** The searcher is a *name resolver*: it is good at
«افتح لي المادة 81 من نظام العمل», where the user names the object. A
government-service question does not name a service, it describes a need —
«بطلع العنوان الوطني لمؤسستي من فين؟ وكيف اقدر اثبت…» (convo ``93fafee7``) —
and the resolver answered it with ten identical ``manual_search`` calls, then a
title typed into ``service_id``. Need-shaped questions call for *find by meaning
→ pick → read*, and that is this module:

1. **Service search** (code, no LLM) — the searcher's ≤3 expansion phrases are
   embedded in ONE batch, each runs ``search_topics(['service'])`` concurrently,
   the per-query lists are fused by reciprocal rank (k=60), deduplicated by
   ``doc_id`` and then by normalized ``service_name_ar``, cut to 15, and
   enriched with ONE ``services`` select + ONE ``service_guides`` select.
   Candidate numbers are minted HERE, in code — and they are the citation
   numbers, so there is one vocabulary and nothing to collide (plan §3).
2. **The compliance agent** (Layer 3, slot ``simple_search_compliance``) — sees
   the numbered candidates, opens ≤6 of them through ``open_service`` under a
   shared char budget (the card whole, the guide sliced beginning/middle/end by
   :func:`agents.simple_search.unfold.slice_head_middle_tail`), and either
   WRITES the artifact (D5: always a card, its length set by the question) or
   returns ``sufficient=false`` with a reason the searcher's next cycle runs on
   (D7 — it never hands off to deep_search itself).

Structure — the house pure / I/O split (``unfold.py``, ``fetch_article.py``):

* **Pure layer** — RRF merge, dedup + cut, candidate lines, citation parsing and
  renumbering, the output validator, reference building. No DB, no model.
* **I/O layer** — :func:`run_service_search`, :func:`open_services` (the tool
  body), :func:`run_compliance`.

**Failure contract.** Infra errors — the embedding call, the RPC, the
candidate select, a ``UsageLimitExceeded`` or a crash inside the agent — RAISE.
They are not judgments about the question, so they must not burn a searcher
cycle; the caller (``runner._answer_loop``, wired separately) turns them into
the deep_search fallback (plan §6, code ``compliance_error``). A search that
genuinely finds nothing returns ``[]``, and :func:`run_compliance` turns zero
candidates into an *insufficient* verdict without spending an LLM call.

This module imports nothing from ``runner.py``: the runner is the one that
imports this, and ``build_references``' service semantics are mirrored here
(see :func:`build_service_reference`) rather than reached for.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.deep_search_v4.aggregator.models import Reference
from agents.simple_search.models import LEVEL_SOURCE_TYPE, ResolvedObject, UnfoldResult
from agents.simple_search.prompts import (
    COMPLIANCE_SYSTEM_PROMPT,
    build_compliance_user_message,
)
from agents.simple_search.unfold import (
    SERVICE_SELECT,
    _fetch_row,
    _fetch_service_guide,
    _fetch_service_guide_images,
    render_service,
    render_service_card,
)
# The house Arabic title normalizer — the same function ``manual_search``'s
# ``rank_candidates`` dedups on (it lives in ``fetch_article`` because the
# dependency may only run downward). Imported, never copied: two normalizers
# would disagree about «و» detachment or a leading «ال» sooner or later.
from agents.tool_repository.fetch_article import _normalize_title
from agents.utils.agent_models import ModelPolicy, get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.tracking import run_tracked, track_stage
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP
from agents.utils.usage_sink import record_search_fee
from shared.db.run import run_db_retry

logger = logging.getLogger(__name__)


# =========================================================================== #
# Constants — every cap carries where it came from (house convention).
# =========================================================================== #

#: ``agents/utils/agent_models.py`` slot — ``_FLASH_MEDIUM``, like the
#: synthesizer it stands in for (plan §4).
COMPLIANCE_SLOT = "simple_search_compliance"

#: ``llm_calls`` / Logfire stage names (plan §8).
COMPLIANCE_STAGE = "simple_search.compliance"
SERVICE_SEARCH_STAGE = "simple_search.service_search"

#: ``llm_calls.agent`` for the flat search fee — one row per service search,
#: ``count`` = queries actually run (0.1 pt each, ``usage_sink``).
SERVICE_SEARCH_FEE_AGENT = "simple_search.search_fee.service_search"

#: D2 — the searcher writes at most three expansion phrases.
MAX_SERVICE_QUERIES = 3

#: ``search_topics`` ``p_per_type``. 15 per query is the RPC default and
#: ``manual_search``'s ``_TOPICS_PER_TYPE``; the RPC already returns ONE row per
#: service (``DISTINCT ON (doc_id)``), so three queries give ≤45 distinct ids.
TOPICS_PER_QUERY = 15

#: Reciprocal-rank-fusion constant. 60 is the standard value and the one the
#: corpus's own hybrid RPCs use (``hybrid_search_cases`` ``rrf_k``). It keeps the
#: fusion rank-shaped rather than score-shaped: a service at ranks 3 and 4 for
#: two queries (1/63 + 1/64) beats one at rank 1 for a single query (1/61).
#: That is the property this merge exists for — 93fafee7's two needs share
#: «العنوان الوطني», and the service that serves both should surface first.
RRF_K = 60

#: D3 — the compliance agent sees at most 15 candidates. Fewer survive → pass
#: what exists; zero is a valid result (an *insufficient* cycle, not an error).
MAX_CANDIDATES = 15

#: Candidate summary clip in the agent's list. 280 chars is ~2 lines of
#: ``intro_description`` — enough to tell «للأفراد» from «للأعمال», which is
#: the distinction the pick turns on, at ≤15 × ~110 tokens for the whole list.
CANDIDATE_INTRO_CHARS = 280

#: D3 — up to 2 main services + up to 4 supporting ones; 6 opens per run.
MAX_PRIMARY = 2
MAX_SUPPORTING = 4
MAX_OPENED_SERVICES = MAX_PRIMARY + MAX_SUPPORTING

#: §5 — the char budget for EVERYTHING ``open_service`` returns in one run:
#: ~26k tokens at ``unfold.ARABIC_CHARS_PER_TOKEN`` (2.75).
COMPLIANCE_OPEN_BUDGET_CHARS = 72_000

#: §5 — no single service gets more than half the budget. Measured over the 825
#: rendered guides (p50 9.0k · p75 14.0k · p90 24.3k · p99 98.9k), a 36k share
#: minus a ≤8k card keeps ~96% of guides whole; six opened at once get 12k each.
MAX_OPEN_SHARE_CHARS = 36_000

#: Reference hover snippet — the runner's ``_REF_SNIPPET_CHARS`` (display only;
#: the model grounds on the opened text, never on this).
_REF_SNIPPET_CHARS = 400

#: Plan §3 — the reason a zero-candidate cycle carries back to the searcher.
NO_CANDIDATES_REASON_AR = "لم تُرجِع عبارات البحث أي خدمة"

_TOPICS_RPC = "search_topics"
_SERVICE_TYPE = "service"

#: The enrichment select — exactly what a candidate line and a reference need.
#: ``url`` is the fallback link, the same ``service_url or url`` rule
#: ``searcher._enrich_services`` applies to a resolved service.
_CANDIDATE_SELECT = "id, service_name_ar, provider_name, intro_description, service_url, url"

#: The «has a guide» badge on a candidate line.
_GUIDE_BADGE_AR = " · يتوفر لها «الدليل الشامل»"


# =========================================================================== #
# Pure layer — candidates.
# =========================================================================== #


@dataclass
class ServiceCandidate:
    """One service the compliance agent may open — its number is its citation.

    ``n`` is 1-based and minted by code (:func:`build_candidates`); the agent
    cites ``[n]`` with exactly this number and code renumbers afterwards
    (:func:`renumber_citations`). ``score`` is the RRF score the list was
    ordered by, not a cosine — it only ranks, it means nothing on its own.
    """

    n: int
    service_id: str
    title: str
    provider: str = ""
    intro: str = ""
    service_url: str = ""
    has_guide: bool = False
    score: float = 0.0
    topic_text: str = ""

    def resolved(self) -> ResolvedObject:
        """The L6 identity the runner, the card and the references speak."""
        return ResolvedObject(
            level="service",
            service_id=self.service_id,
            title=self.title,
            subtitle=self.provider,
            source_url=self.service_url,
        )


@dataclass
class ServiceHit:
    """One service after reciprocal-rank fusion — before enrichment.

    ``title`` and ``topic_text`` come from the RPC row with the best raw score.
    Note the RPC's ``title`` is ``COALESCE(intro_title, service_name_ar)`` —
    NOT the service name — which is why the title dedup runs after enrichment,
    on ``services.service_name_ar`` (:func:`dedup_and_cut`).
    """

    doc_id: str
    title: str = ""
    topic_text: str = ""
    best_score: float = 0.0
    rrf: float = 0.0
    hits: int = 0
    first_seen: int = 0


def clean_queries(queries: Iterable[str] | None) -> list[str]:
    """Strip, drop blanks, dedupe (exact, after whitespace collapse), cap at 3."""
    out: list[str] = []
    for raw in queries or []:
        q = " ".join(str(raw or "").split())
        if q and q not in out:
            out.append(q)
    if len(out) > MAX_SERVICE_QUERIES:
        logger.info(
            "simple_search compliance: %d service queries, keeping the first %d",
            len(out), MAX_SERVICE_QUERIES,
        )
    return out[:MAX_SERVICE_QUERIES]


def _row_doc_id(row: dict[str, Any]) -> str:
    # ``doc_id`` IS ``services.id`` for ``source_type='service'``; ``source_id``
    # is the same join key and is the fallback should a re-ingest ever blank it.
    return str(row.get("doc_id") or row.get("source_id") or "").strip()


def rrf_merge(
    result_lists: Sequence[Sequence[dict[str, Any]]], *, k: int = RRF_K
) -> list[ServiceHit]:
    """Fuse per-query ``search_topics`` lists by reciprocal rank. Pure.

    Each list contributes ``1 / (k + rank)`` per service, rank 1-based over the
    DISTINCT services of that list (a repeated ``doc_id`` inside one list counts
    once, at its best rank). Ordered by fused score, then best raw cosine, then
    first appearance — fully deterministic, so a test pins the exact order.
    """
    fused: dict[str, ServiceHit] = {}
    order = 0
    for rows in result_lists:
        seen: set[str] = set()
        rank = 0
        for row in rows or []:
            doc_id = _row_doc_id(row)
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            rank += 1
            score = float(row.get("score") or 0.0)
            hit = fused.get(doc_id)
            if hit is None:
                hit = ServiceHit(doc_id=doc_id, first_seen=order)
                order += 1
                fused[doc_id] = hit
            hit.rrf += 1.0 / (k + rank)
            hit.hits += 1
            if hit.hits == 1 or score > hit.best_score:
                hit.best_score = score
                hit.title = str(row.get("title") or "").strip()
                hit.topic_text = " ".join(str(row.get("topic_text") or "").split())
    return sorted(
        fused.values(), key=lambda h: (-h.rrf, -h.best_score, h.first_seen)
    )


def dedup_and_cut(
    hits: Sequence[ServiceHit],
    rows_by_id: dict[str, dict[str, Any]] | None = None,
    *,
    limit: int = MAX_CANDIDATES,
) -> list[ServiceHit]:
    """Dedup by ``doc_id``, then by normalized ``service_name_ar``; cut. Pure.

    ``rows_by_id`` is the enrichment select. When it is given, a hit with no
    ``services`` row is DROPPED (it cannot be opened, so offering it is offering
    a dead number), and the title compared is the row's ``service_name_ar``.
    Without it the RPC title is compared instead. A blank normalized title never
    collapses anything.

    Why ``service_name_ar`` and not the cleaner ``intro_title``: measured live
    2026-10-28, ``service_name_ar`` is «{الجهة} - {اسم الخدمة}» on 4,944 of
    4,985 rows, so this key is effectively (provider, title). It collapses the
    same-provider duplicates (98 exact-name groups, e.g. one التأمينات service
    listed 4×) and keeps apart the 136 ``intro_title`` groups that span
    providers — «حجز موعد» under 7 different entities is 7 different services,
    and which entity it is decides whether the answer is right.
    """
    out: list[ServiceHit] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for hit in hits:
        if hit.doc_id in seen_ids:
            continue
        row: dict[str, Any] | None = None
        if rows_by_id is not None:
            row = rows_by_id.get(hit.doc_id)
            if not row:
                continue
        name = (row or {}).get("service_name_ar") or hit.title
        norm = _normalize_title(str(name or ""))
        if norm and norm in seen_titles:
            continue
        seen_ids.add(hit.doc_id)
        if norm:
            seen_titles.add(norm)
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def build_candidates(
    hits: Sequence[ServiceHit],
    rows_by_id: dict[str, dict[str, Any]],
    guided_ids: set[str] | None = None,
) -> list[ServiceCandidate]:
    """Mint the numbered candidates, 1..n in fused order. Pure."""
    guided = guided_ids or set()
    out: list[ServiceCandidate] = []
    for hit in hits:
        row = rows_by_id.get(hit.doc_id) or {}
        out.append(
            ServiceCandidate(
                n=len(out) + 1,
                service_id=hit.doc_id,
                title=str(row.get("service_name_ar") or hit.title or "").strip(),
                provider=str(row.get("provider_name") or "").strip(),
                intro=" ".join(str(row.get("intro_description") or "").split()),
                service_url=str(row.get("service_url") or row.get("url") or "").strip(),
                has_guide=hit.doc_id in guided,
                score=round(hit.rrf, 6),
                topic_text=hit.topic_text,
            )
        )
    return out


def _label(c: ServiceCandidate) -> str:
    """``{title} — {provider}``, unless the title already NAMES the provider.

    Measured live 2026-10-28: 4,944 of 4,985 ``service_name_ar`` values are
    «{الجهة} - {اسم الخدمة}» (``searcher._enrich_services``: "service_name_ar
    IS the whole card"), so the plan's ``— {provider_name}`` suffix would print
    the entity twice on almost every line. The title stays ``service_name_ar``
    (it is the card's own label); only the redundant suffix is dropped.
    """
    if c.provider and not c.title.startswith(c.provider):
        return f"{c.title} — {c.provider}"
    return c.title


def render_candidate_lines(cands: Sequence[ServiceCandidate]) -> list[str]:
    """``[n] {title} — {provider}: {intro[:280]}`` (+ the guide badge). Pure.

    One line per candidate — the intro is whitespace-collapsed for exactly that
    reason. A blank provider or intro drops its separator rather than printing
    «— :»; a blank intro falls back to the RPC's short topic phrase, so no line
    is a bare title. Escaping is the message builder's job, not this one's.
    """
    lines: list[str] = []
    for c in cands:
        summary = " ".join((c.intro or c.topic_text or "").split())
        if len(summary) > CANDIDATE_INTRO_CHARS:
            summary = summary[:CANDIDATE_INTRO_CHARS].rstrip() + "…"
        line = f"[{c.n}] {_label(c)}"
        if summary:
            line += f": {summary}"
        if c.has_guide:
            line += _GUIDE_BADGE_AR
        lines.append(line)
    return lines


# =========================================================================== #
# Pure layer — citations.
# =========================================================================== #

#: MIRRORS ``runner._CITATION_MARKER_RE`` (itself mirroring
#: ``postvalidator._CITATION_RE``): ``[1]``, ``[1,3]``, ``[١،٣]`` — Western or
#: Arabic-Indic digits, comma or Arabic comma. Not imported, because the runner
#: imports THIS module; if one changes, change both (a test pins them equal).
_CITATION_MARKER_RE = re.compile(r"\[\s*[\d٠-٩]+(?:\s*[,،]\s*[\d٠-٩]+)*\s*\]")
_NUMBER_RE = re.compile(r"[\d٠-٩]+")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _marker_numbers(marker: str) -> list[int]:
    return [int(tok.translate(_ARABIC_INDIC)) for tok in _NUMBER_RE.findall(marker)]


def cited_in(md: str) -> list[int]:
    """Every number cited in ``md``, in order of FIRST appearance, deduped. Pure."""
    out: list[int] = []
    for match in _CITATION_MARKER_RE.finditer(md or ""):
        for n in _marker_numbers(match.group(0)):
            if n not in out:
                out.append(n)
    return out


def renumber_citations(
    md: str, cited: Iterable[int] | None = None
) -> tuple[str, dict[int, int]]:
    """Rewrite candidate-number citations to a contiguous ``1..k``. Pure.

    Numbers are assigned in order of FIRST appearance in ``md`` — the reader
    meets ``[1]`` first, then ``[2]``, whatever the candidates were numbered.
    Every marker is rewritten in canonical form: Western digits, deduped,
    ascending, comma-joined with no space (``[1,3]`` — the form
    ``_CITATION_RULES`` mandates), so an Arabic-Indic or Arabic-comma marker the
    model slipped through comes out clickable.

    Args:
        md: the artifact as the agent wrote it, citing candidate numbers.
        cited: optional allow-list (``primary ∪ supporting``). A number outside
            it is removed from its marker, and a marker left empty is removed
            whole. The validator already rejects strays, so on the happy path
            this is a no-op net.

    Returns:
        ``(new_md, mapping)`` where ``mapping`` is ``{candidate n: new n}`` in
        new-number order. Its keys are exactly the services the artifact cites.
    """
    allowed = None if cited is None else {int(n) for n in cited}
    mapping: dict[int, int] = {}
    for n in cited_in(md):
        if allowed is not None and n not in allowed:
            continue
        mapping[n] = len(mapping) + 1

    def _rewrite(match: "re.Match[str]") -> str:
        new = sorted({mapping[n] for n in _marker_numbers(match.group(0)) if n in mapping})
        return "[" + ",".join(str(n) for n in new) + "]" if new else ""

    new_md = _CITATION_MARKER_RE.sub(_rewrite, md or "")
    if allowed is not None:
        # A marker removed whole leaves «النص  .» behind — same tidy-up as
        # ``runner._strip_citation_markers``.
        new_md = re.sub(r"[ \t]{2,}", " ", new_md)
        new_md = re.sub(r"[ \t]+([.،,؛:])", r"\1", new_md)
    return new_md, mapping


def _fmt_numbers(numbers: Iterable[int]) -> str:
    """``[3]، [7]`` — Latin digits, Arabic comma (house policy)."""
    return "، ".join(f"[{n}]" for n in numbers)


def _dedupe(numbers: Iterable[int]) -> list[int]:
    out: list[int] = []
    for n in numbers or []:
        if n not in out:
            out.append(n)
    return out


# =========================================================================== #
# The agent — deps, output, validator.
# =========================================================================== #


@dataclass
class ComplianceDeps:
    """Runtime state for ONE compliance run — rebuilt per run, never shared.

    ``opened`` is ordered (the order the agent opened in) and doubles as the
    "opened set" the validator checks ``primary`` against. ``opened_text`` keeps
    each rendered block so a cited service's reference snippet is what the agent
    actually read; ``sliced`` records which guides lost text to the budget.
    """

    supabase: Any
    conversation_id: str = ""
    case_id: str | None = None
    candidates: dict[int, ServiceCandidate] = field(default_factory=dict)
    opened: list[int] = field(default_factory=list)
    budget_remaining: int = COMPLIANCE_OPEN_BUDGET_CHARS
    opened_text: dict[int, str] = field(default_factory=dict)
    sliced: set[int] = field(default_factory=set)

    def tracking_input(self) -> dict[str, Any]:
        """Bounded span attributes — never the client, never candidate text."""
        return {
            "candidates": len(self.candidates),
            "opened": len(self.opened),
            "budget_remaining": self.budget_remaining,
        }


class ComplianceOutput(BaseModel):
    """What the compliance agent returns — an artifact, or a reasoned revert.

    The numbers are CANDIDATE numbers throughout; code renumbers the citations
    after the run (:func:`renumber_citations`). ``sufficient`` has no default on
    purpose: a model that forgets to rule must be retried, never read as either
    verdict.
    """

    sufficient: bool = Field(
        description=(
            "True when the opened services answer the question. False sends the "
            "turn back to the search step with insufficient_reason."
        ),
    )
    insufficient_reason: str = Field(
        default="",
        description=(
            "Arabic, specific: what the question needed and what the candidates "
            "offered instead. The next search round runs on this text. Empty "
            "when sufficient=true."
        ),
    )
    primary: list[int] = Field(
        default_factory=list,
        description="1–2 candidate numbers — the main services, each OPENED.",
    )
    supporting: list[int] = Field(
        default_factory=list,
        description="0–4 candidate numbers — prerequisites or follow-on steps.",
    )
    artifact_title: str = Field(
        default="",
        description="Short Arabic noun phrase naming the outcome (≤ 80 chars).",
    )
    artifact_md: str = Field(
        default="",
        description=(
            "The Arabic artifact, citing [n] with candidate numbers. Empty only "
            "when sufficient=false."
        ),
    )
    used_refs: list[int] = Field(
        default_factory=list,
        description="The candidate numbers cited as [n] in artifact_md.",
    )


# The retry message enumerates THIS schema's fields — the synthesizer's lesson
# (``synthesizer._SYNTH_RETRY_MSG``): a salvage retry naming another schema's
# fields teaches the model to emit keys this one rejects. Keep identical to
# ``ComplianceOutput.model_fields`` (a test pins it).
_COMPLIANCE_RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(sufficient, insufficient_reason, primary, supporting, artifact_title, "
    "artifact_md, used_refs) only — with no text and no <thinking> tag outside "
    "the JSON. The `artifact_md`, `artifact_title` and `insufficient_reason` "
    "values must be in Arabic."
)


def _compliance_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the ``output_type`` union."""
    return TextOutput(
        make_json_salvager(ComplianceOutput, retry_msg=_COMPLIANCE_RETRY_MSG)
    )


#: Requests: one open + one answer is the happy path; two opens + validator
#: retries fit in 6. Tool calls: 4 is two rounds of opens with room to correct
#: (each call may open several numbers — the 6-service cap is separate and is
#: enforced inside the tool as a refusal, never a raise). Either limit firing is
#: an INFRA failure → deep_search fallback (plan §4), not a judgment.
COMPLIANCE_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,  # shared runaway net — usage_limits.py
    request_limit=6,
    tool_calls_limit=4,
)


def validate_compliance_output(
    output: ComplianceOutput, deps: ComplianceDeps
) -> ComplianceOutput:
    """The output validator, pure: Arabic ``ModelRetry`` per violation (plan §4).

    Insufficient → only the reason is checked; its numbers are discarded by the
    caller anyway, so retrying a junk ``primary`` there would spend a request on
    data nobody reads. Sufficient → numbers must be candidates; ``primary`` is
    1–2 and every primary was OPENED; ``supporting`` ≤ 4; the artifact is
    non-empty; every ``[n]`` in it is in ``primary ∪ supporting``; and every
    primary is cited at least once (a main service with no citation would leave
    the card's المراجع panel without the very service the answer is about).

    Duplicates are normalized, not retried: ``[3, 3]`` → ``[3]``, and a number
    in both lists stays primary only. Returns the normalized output.
    """
    if not output.sufficient:
        if not output.insufficient_reason.strip():
            raise ModelRetry(
                "عند sufficient=false اكتب insufficient_reason بالعربية: ما الذي "
                "احتاجه السؤال، وما الذي عرضه المرشحون بدلًا منه. جولة البحث "
                "التالية تعمل على هذا السبب وحده."
            )
        return output

    known = set(deps.candidates)
    primary = _dedupe(output.primary)
    supporting = [n for n in _dedupe(output.supporting) if n not in primary]
    body = cited_in(output.artifact_md)

    unknown = sorted(
        {n for n in (*primary, *supporting, *output.used_refs, *body) if n not in known}
    )
    if unknown:
        raise ModelRetry(
            f"الأرقام {_fmt_numbers(unknown)} ليست بين المرشحين. استخدم أرقام "
            f"<candidates> فقط (من 1 إلى {len(known)})، واكتب [n] للاستشهاد "
            "بخدمة فقط — لا تضع أي رقم آخر بين معقوفين."
        )
    if not primary:
        raise ModelRetry(
            "sufficient=true يتطلب خدمة رئيسية واحدة على الأقل في primary، "
            "مفتوحة عبر open_service."
        )
    if len(primary) > MAX_PRIMARY:
        raise ModelRetry(
            "primary يقبل خدمتين رئيسيتين على الأكثر. إن احتاج "
            "السؤال 3 خدمات رئيسية أو أكثر فأعد sufficient=false مع "
            "insufficient_reason «يحتاج أكثر من خدمتين رئيسيتين» وذكر الاحتياجات."
        )
    unopened = [n for n in primary if n not in deps.opened]
    if unopened:
        hint = (
            "افتحها عبر open_service قبل الكتابة عنها."
            if len(deps.opened) < MAX_OPENED_SERVICES
            else (
                f"بلغت حد الفتح ({MAX_OPENED_SERVICES} خدمات)، فاختر الخدمة "
                f"الرئيسية من المفتوحة: {_fmt_numbers(deps.opened)}، أو أعد "
                "sufficient=false مع السبب."
            )
        )
        raise ModelRetry(
            f"الخدمة الرئيسية {_fmt_numbers(unopened)} لم تُفتح. {hint}"
        )
    if len(supporting) > MAX_SUPPORTING:
        raise ModelRetry(
            f"supporting يقبل {MAX_SUPPORTING} خدمات مساندة على الأكثر؛ أبقِ "
            "ما لا تكتمل الإجابة بدونه فقط."
        )
    if not output.artifact_md.strip():
        raise ModelRetry(
            "artifact_md فارغ. عند sufficient=true اكتب الإجابة بالعربية من "
            "الخدمات المفتوحة، مع الاستشهاد بالصيغة [n]."
        )
    allowed = set(primary) | set(supporting)
    stray = [n for n in body if n not in allowed]
    if stray:
        raise ModelRetry(
            f"الاستشهادات {_fmt_numbers(stray)} في artifact_md ليست في primary "
            "ولا في supporting. أضفها إلى supporting إن كان المستخدم يحتاجها، "
            "أو احذف الاستشهاد."
        )
    uncited = [n for n in primary if n not in body]
    if uncited:
        raise ModelRetry(
            f"لم يُستشهد بالخدمة الرئيسية {_fmt_numbers(uncited)} في artifact_md. "
            "استشهد بكل خدمة رئيسية مرة واحدة على الأقل بالصيغة [n]."
        )
    return output.model_copy(update={"primary": primary, "supporting": supporting})


# =========================================================================== #
# The tool body — open_service (I/O + run state).
# =========================================================================== #


async def _open_one(
    supabase: Any, cand: ServiceCandidate, share: int
) -> UnfoldResult | None:
    """Fetch + render ONE service under ``share`` chars; ``None`` when no row.

    The same reads as ``unfold.unfold_service`` (row → guide → images, the image
    read only after a guide hit), through the same best-effort helpers. The card
    is measured FIRST so the guide's budget is the share minus the card — plan
    §5: the card stays whole, the guide gets the rest (floor 0).
    """
    row = await asyncio.to_thread(
        _fetch_row, supabase, "services", "id", cand.service_id, SERVICE_SELECT
    )
    if not row:
        return None
    guide = await asyncio.to_thread(_fetch_service_guide, supabase, cand.service_id)
    images: list[dict[str, Any]] = []
    if guide.get("id"):
        images = await asyncio.to_thread(
            _fetch_service_guide_images, supabase, str(guide.get("id"))
        )
    card_chars = len(render_service_card(row).strip())
    return render_service(
        row,
        guide_row=guide,
        image_rows=images,
        guide_budget_chars=max(0, share - card_chars),
    )


def _render_open_block(n: int, cand: ServiceCandidate, unfolded: UnfoldResult) -> str:
    head = f"## [{n}] {_label(cand)}"
    if "guide_sliced" in unfolded.notes:
        head += (
            "\n(«الدليل الشامل» لهذه الخدمة أطول من حصتها: عُرض أوله ووسطه وآخره، "
            "وكل موضع حُذف منه معلَّم.)"
        )
    return f"{head}\n\n{unfolded.text}"


async def open_services(deps: ComplianceDeps, numbers: Sequence[int]) -> str:
    """The ``open_service`` tool body — open candidates under the cap + budget.

    Never raises on a bad request: an unknown number, a re-open and the cap all
    come back as Arabic lines the model reacts to on its next turn (the
    ``edit_artifact`` house rule — a refusal string, not a burned retry).

    * **Cap** — :data:`MAX_OPENED_SERVICES` distinct services per run. Numbers
      beyond the remaining slots are refused, in the order asked.
    * **Re-open** — «سبق فتح الخدمة [n]», no budget spent.
    * **Budget** — every NEW service in this call gets the same share,
      ``min(MAX_OPEN_SHARE_CHARS, remaining // len(new))``; what is actually
      rendered (card + sliced guide) is deducted, so a short guide leaves budget
      for a later call. The card is never cut, so a call made on an empty
      budget still shows each card — its guide arrives as label + gap marker.
    """
    requested: list[int] = []
    for raw in numbers or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n not in requested:
            requested.append(n)
    if not requested:
        return (
            "لم تحدّد أي رقم خدمة. مرّر أرقام المرشحين كما تظهر في <candidates>، "
            "مثل open_service([2])."
        )

    notes: list[str] = []
    fresh: list[int] = []
    for n in requested:
        if n not in deps.candidates:
            notes.append(
                f"الرقم [{n}] ليس بين المرشحين؛ الأرقام المتاحة من 1 إلى "
                f"{len(deps.candidates)}."
            )
        elif n in deps.opened:
            notes.append(f"سبق فتح الخدمة [{n}] — نصّها فيما سبق من هذه المحادثة.")
        else:
            fresh.append(n)

    slots = max(0, MAX_OPENED_SERVICES - len(deps.opened))
    allowed, refused = fresh[:slots], fresh[slots:]
    if refused:
        notes.append(
            f"بلغت الحد الأقصى لفتح الخدمات ({MAX_OPENED_SERVICES} خدمات في "
            f"الإجابة الواحدة)؛ لم تُفتح: {_fmt_numbers(refused)}. اكتب من "
            "الخدمات المفتوحة، أو أعد sufficient=false مع السبب."
        )

    blocks: list[str] = []
    if allowed:
        share = min(MAX_OPEN_SHARE_CHARS, deps.budget_remaining // len(allowed))
        rendered = await asyncio.gather(
            *(_open_one(deps.supabase, deps.candidates[n], share) for n in allowed)
        )
        for n, unfolded in zip(allowed, rendered):
            cand = deps.candidates[n]
            if unfolded is None:
                # Not opened, not counted: the slot and the budget stay free, and
                # the validator will refuse it as primary.
                notes.append(
                    f"تعذّر فتح الخدمة [{n}] «{cand.title}»؛ لا تجعلها خدمة رئيسية."
                )
                continue
            deps.opened.append(n)
            deps.budget_remaining = max(0, deps.budget_remaining - unfolded.chars)
            deps.opened_text[n] = unfolded.text
            if "guide_sliced" in unfolded.notes:
                deps.sliced.add(n)
            blocks.append(_render_open_block(n, cand, unfolded))

    footer = (
        f"فُتحت {len(deps.opened)} من {MAX_OPENED_SERVICES} خدمات في هذه الإجابة؛ "
        f"المتبقي من ميزانية الفتح: {deps.budget_remaining} حرف."
    )
    return "\n\n".join([*blocks, *notes, footer])


# =========================================================================== #
# Agent factory.
# =========================================================================== #


def create_compliance_agent(
    model_override: ModelPolicy | str | None = None,
) -> Agent[ComplianceDeps, ComplianceOutput]:
    """Build a fresh compliance agent (one per run — no state survives a run).

    ``retries`` / ``output_retries`` match the synthesizer (2 / 3). The tool
    itself never raises ``ModelRetry`` — a bad number is a refusal line — so
    ``retries`` only covers malformed tool ARGUMENTS.
    """
    agent: Agent[ComplianceDeps, ComplianceOutput] = Agent(
        get_agent_model(COMPLIANCE_SLOT, model_override),
        name="simple_search_compliance",
        deps_type=ComplianceDeps,
        output_type=[ComplianceOutput, _compliance_text_output()],
        instructions=COMPLIANCE_SYSTEM_PROMPT,
        retries=2,
        output_retries=3,
    )

    @agent.tool
    async def open_service(  # noqa: RUF029 — fetches run in asyncio.to_thread
        ctx: RunContext[ComplianceDeps], numbers: list[int]
    ) -> str:
        """Open candidate services by their numbers: card + «الدليل الشامل».

        Pass every number you need in ONE call where you can, e.g. [2, 5]. At
        most 6 services per answer; the opening budget is shared, so the more
        you open at once the less of each long guide you see. A long guide comes
        back with parts removed from the inside, each removal marked
        «[حُذف جزء من الدليل هنا…]» — its beginning, middle and end are kept.

        Args:
            numbers: candidate numbers exactly as listed in <candidates>.
        """
        return await open_services(ctx.deps, numbers)

    @agent.output_validator
    def _validate(
        ctx: RunContext[ComplianceDeps], output: ComplianceOutput
    ) -> ComplianceOutput:
        return validate_compliance_output(output, ctx.deps)

    return agent


# =========================================================================== #
# Result.
# =========================================================================== #


@dataclass
class ComplianceResult:
    """What :func:`run_compliance` hands the runner.

    Sufficient: ``artifact_md`` is already RENUMBERED — its ``[1]..[k]`` match
    ``references`` (cited services only, in first-appearance order) and
    ``cited_numbers`` (``[1..k]``, what the runner passes as ``used_refs``).
    ``citation_map`` keeps candidate n → new n for telemetry and tests.
    Insufficient: ``reason`` is what the searcher's next cycle runs on.
    ``opened`` (candidate numbers, in opening order) and ``sliced`` are filled
    either way.
    """

    sufficient: bool
    reason: str = ""
    title: str = ""
    artifact_md: str = ""
    references: list[Reference] = field(default_factory=list)
    cited_numbers: list[int] = field(default_factory=list)
    citation_map: dict[int, int] = field(default_factory=dict)
    primary_objects: list[ResolvedObject] = field(default_factory=list)
    supporting_objects: list[ResolvedObject] = field(default_factory=list)
    opened: list[int] = field(default_factory=list)
    sliced: bool = False

    @property
    def truncated(self) -> bool:
        """Alias of :attr:`sliced` — the responder digest's word for it."""
        return self.sliced


def build_service_reference(n: int, obj: ResolvedObject, snippet: str) -> Reference:
    """One ``Reference`` for a cited service — ``runner.build_references``' L6 row.

    Mirrored field for field for ``level == "service"``: ``source_type`` from
    ``LEVEL_SOURCE_TYPE`` (``gov_service``, not the domain value), the parent
    slot is the PROVIDER (``regulation_title``, the house mapping for a خدمة),
    ``domain``/``ref_id`` from the object, the official link in
    ``service_url`` — and the landing/details/entity slots empty, because a
    service fills none of them.
    """
    return Reference(
        n=n,
        source_type=LEVEL_SOURCE_TYPE[obj.level],  # type: ignore[arg-type]
        regulation_title=obj.subtitle or obj.label_ar(),
        title=obj.title or obj.label_ar(),
        article_num=obj.article_number or None,
        snippet=(snippet or "")[:_REF_SNIPPET_CHARS],
        relevance="high",
        ref_id=obj.ref_id(),
        domain=obj.domain(),  # type: ignore[arg-type]
        doc_type=obj.doc_type,
        entity_name="",
        landing_url="",
        details_url="",
        service_url=obj.source_url,
    )


def build_compliance_result(
    output: ComplianceOutput, deps: ComplianceDeps
) -> ComplianceResult:
    """Turn a validated output into the runner's :class:`ComplianceResult`. Pure."""
    opened = list(deps.opened)
    sliced = bool(deps.sliced)
    if not output.sufficient:
        return ComplianceResult(
            sufficient=False,
            reason=output.insufficient_reason.strip(),
            opened=opened,
            sliced=sliced,
        )

    primary = [n for n in _dedupe(output.primary) if n in deps.candidates]
    supporting = [
        n for n in _dedupe(output.supporting)
        if n in deps.candidates and n not in primary
    ]
    md, mapping = renumber_citations(output.artifact_md, primary + supporting)

    references: list[Reference] = []
    for old_n, new_n in mapping.items():
        cand = deps.candidates[old_n]
        snippet = deps.opened_text.get(old_n) or cand.intro or cand.topic_text
        references.append(build_service_reference(new_n, cand.resolved(), snippet))

    title = output.artifact_title.strip()
    if not title and primary:
        title = deps.candidates[primary[0]].title

    return ComplianceResult(
        sufficient=True,
        title=title,
        artifact_md=md.strip(),
        references=references,
        cited_numbers=list(mapping.values()),
        citation_map=dict(mapping),
        primary_objects=[deps.candidates[n].resolved() for n in primary],
        supporting_objects=[deps.candidates[n].resolved() for n in supporting],
        opened=opened,
        sliced=sliced,
    )


# =========================================================================== #
# I/O layer — service search.
# =========================================================================== #

EmbedManyFn = Callable[[list[str]], Awaitable[list[list[float]]]]


def _house_embed_many() -> EmbedManyFn:
    # Lazy: the import builds nothing, but the CLIENT needs ALIBABA_API_KEY, and
    # a test that injects ``embed_many`` must never need it. The 1024-d
    # ``text-embedding-v4`` space is the one ``search_topics`` was built in.
    from agents.utils.embeddings import embed_regulation_queries_alibaba

    return embed_regulation_queries_alibaba


def _search_services(supabase: Any, embedding: list[float]) -> list[dict[str, Any]]:
    """One ``search_topics`` call restricted to services. RAISES on failure.

    Unlike ``manual_search._topics`` this does not swallow: an RPC failure here
    is infra, and the caller's contract is to fall back to deep_search rather
    than mistake an outage for "no service matched".
    """
    resp = supabase.rpc(
        _TOPICS_RPC,
        {
            "p_query_embedding": embedding,
            "p_types": [_SERVICE_TYPE],
            "p_per_type": TOPICS_PER_QUERY,
        },
    ).execute()
    return [r for r in (getattr(resp, "data", None) or []) if isinstance(r, dict)]


def _fetch_service_rows(supabase: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    """ONE batched ``services`` select → ``{id: row}``. RAISES on failure.

    ≤45 ids (3 queries × 15) — far under the 150-id ``in_`` URL-length batch.
    """
    if not ids:
        return {}
    resp = (
        supabase.table("services")
        .select(_CANDIDATE_SELECT)
        .in_("id", ids)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}


def _fetch_guided_service_ids(supabase: Any, ids: list[str]) -> set[str]:
    """ONE ``service_guides`` select → the ids that have a canonical guide.

    Best-effort, unlike the two reads above: a miss only loses the «الدليل
    الشامل» badge on a line, and ``open_service`` reads the guide for itself
    anyway, so an outage here must not cost the turn.
    """
    if not ids:
        return set()
    try:
        resp = (
            supabase.table("service_guides")
            .select("service_id")
            .in_("service_id", ids)
            .eq("is_canonical", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — badge only
        logger.warning("simple_search compliance: guide badge lookup failed: %s", exc)
        return set()
    rows = getattr(resp, "data", None) or []
    return {str(r.get("service_id")) for r in rows if isinstance(r, dict) and r.get("service_id")}


async def run_service_search(
    supabase: Any,
    queries: list[str],
    *,
    embed_many: EmbedManyFn | None = None,
    conversation_id: str = "",
    case_id: str | None = None,
) -> list[ServiceCandidate]:
    """Expansion phrases → 0–15 numbered service candidates (plan §3).

    ONE batched embedding call; one ``search_topics(['service'])`` per query,
    concurrently, each through ``run_db_retry`` (sync client in a thread); ONE
    search fee for all of them; RRF merge; ONE ``services`` select for every
    fused id (the title dedup needs ``service_name_ar``, which the RPC's
    ``title`` is not); dedup + cut to 15; ONE ``service_guides`` select for the
    badge.

    **Every read goes through ``run_db_retry``, not a bare ``to_thread``.** The
    three searches share the one sync client concurrently, and a single dead
    keepalive socket (``httpx.ReadError`` — measured: 3 of 15 live eval cases on
    2026-10-28) used to fail the whole services search and send a perfectly
    answerable question to deep_search. All four calls are reads, so the retry
    contract (idempotent ``fn``) holds trivially; only transport failures are
    retried, a 4xx still raises on attempt 1.

    Raises:
        Whatever the embedder, the RPC or the ``services`` select raise after
        the retries — infra, which the caller turns into the deep_search
        fallback. Zero hits is NOT an error: it returns ``[]``.

    The fee is recorded only once the searches have actually run, with
    ``count`` = the queries searched (after blank-stripping, dedupe and the
    3-cap) — an embedding outage bills nothing.
    """
    cleaned = clean_queries(queries)
    if not cleaned:
        return []

    with track_stage(
        SERVICE_SEARCH_STAGE,
        conversation_id=conversation_id or None,
        case_id=case_id,
        agent_family="simple_search",
        queries=len(cleaned),
    ) as span:
        embed = embed_many or _house_embed_many()
        vectors = await embed(cleaned)
        if len(vectors) != len(cleaned):
            raise RuntimeError(
                f"service search: {len(cleaned)} queries embedded into "
                f"{len(vectors)} vectors"
            )
        result_lists = await asyncio.gather(
            *(run_db_retry(_search_services, supabase, vec) for vec in vectors)
        )
        record_search_fee(
            agent=SERVICE_SEARCH_FEE_AGENT,
            agent_family="simple_search",
            count=len(cleaned),
        )

        fused = rrf_merge(result_lists)
        if not fused:
            span.set(raw_hits=0, candidates=0)
            return []
        rows = await run_db_retry(
            _fetch_service_rows, supabase, [h.doc_id for h in fused]
        )
        picked = dedup_and_cut(fused, rows)
        guided = await run_db_retry(
            _fetch_guided_service_ids, supabase, [h.doc_id for h in picked]
        )
        candidates = build_candidates(picked, rows, guided)
        span.set(
            raw_hits=sum(len(r) for r in result_lists),
            fused=len(fused),
            candidates=len(candidates),
            with_guide=sum(1 for c in candidates if c.has_guide),
        )
        return candidates


# =========================================================================== #
# I/O layer — the compliance run.
# =========================================================================== #


async def run_compliance(
    question: str,
    candidates: Sequence[ServiceCandidate],
    *,
    supabase: Any,
    conversation_id: str,
    case_id: str | None,
    recent_messages: list | None = None,
    detail_level: str = "",
    rejection_notes: list[str] | None = None,
    model_override: ModelPolicy | str | None = None,
) -> ComplianceResult:
    """Run the compliance agent over ``candidates`` for ``question``.

    Zero candidates short-circuits to an *insufficient* result carrying
    :data:`NO_CANDIDATES_REASON_AR` — no LLM call (plan §3: zero is a cycle the
    searcher re-queries, not an error).

    Raises:
        ``UsageLimitExceeded``, ``UnexpectedModelBehavior`` (output retries
        exhausted) and any provider error, untouched — the caller treats them as
        infra and falls back to deep_search (plan §4/§6).
    """
    if not candidates:
        return ComplianceResult(sufficient=False, reason=NO_CANDIDATES_REASON_AR)

    deps = ComplianceDeps(
        supabase=supabase,
        conversation_id=conversation_id or "",
        case_id=case_id,
        candidates={c.n: c for c in candidates},
    )
    message = build_compliance_user_message(
        question,
        render_candidate_lines(candidates),
        recent_messages=recent_messages,
        detail_level=detail_level,
        rejection_notes=rejection_notes,
    )
    agent = create_compliance_agent(model_override)
    result = await run_tracked(
        agent,
        message,
        deps=deps,
        stage=COMPLIANCE_STAGE,
        slot=COMPLIANCE_SLOT,
        agent_family="simple_search",
        usage_limits=COMPLIANCE_LIMITS,
    )
    return build_compliance_result(result.output, deps)


__all__ = [
    "COMPLIANCE_SLOT",
    "COMPLIANCE_STAGE",
    "SERVICE_SEARCH_STAGE",
    "SERVICE_SEARCH_FEE_AGENT",
    "COMPLIANCE_LIMITS",
    "COMPLIANCE_OPEN_BUDGET_CHARS",
    "MAX_OPEN_SHARE_CHARS",
    "MAX_OPENED_SERVICES",
    "MAX_PRIMARY",
    "MAX_SUPPORTING",
    "MAX_SERVICE_QUERIES",
    "MAX_CANDIDATES",
    "RRF_K",
    "NO_CANDIDATES_REASON_AR",
    "ServiceCandidate",
    "ServiceHit",
    "ComplianceDeps",
    "ComplianceOutput",
    "ComplianceResult",
    "clean_queries",
    "rrf_merge",
    "dedup_and_cut",
    "build_candidates",
    "render_candidate_lines",
    "cited_in",
    "renumber_citations",
    "validate_compliance_output",
    "build_service_reference",
    "build_compliance_result",
    "open_services",
    "create_compliance_agent",
    "run_service_search",
    "run_compliance",
]
