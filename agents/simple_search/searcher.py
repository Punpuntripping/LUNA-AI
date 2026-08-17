"""The ``simple_search`` searcher — Layer 2, the whole retrieval loop (§2.1).

The searcher works out **which legal object** the user is pointing at and hands
its identity on. Five responsibilities, per plan §2.1:

1. **Determine the data type** — ``regs | judgments | services | circulars |
   article`` — and carry it with every downstream request.
2. **Resolve the object.** Deterministic first: ``fetch_article``'s title
   resolver for regulations, ``articles_v2`` exact-text for مواد.
3. **Manual search** — registered from :mod:`agents.simple_search.manual_search`
   (C2, its own plan). Its scope is asymmetric: ``fetch_article`` is the repo's
   only identity resolver and it covers regulations and articles alone, so for
   judgments / services / circulars manual search is not a fallback — it is the
   **primary and only** path.
4. **``ask_user``** when resolution stays ambiguous. Deferred tool + the
   ``paused_runs`` machinery, exactly the planner's pattern
   (``planner/agent.py:140``).
5. **Abort out-of-scope turns back to the router** via
   :attr:`SearcherDecision.aborted`, mirroring ``PlannerDecision.aborted``
   (``planner/models.py:122-129``).

**Hands off identity, never content.** The searcher's product is the resolved
object — ids + level + the display fields it matched on. It never calls the full
:func:`agents.simple_search.unfold.unfold`; that is the synthesizer's input path
and it never lived here. The only unfold the searcher sees is
``unfold(preview)``: the snippet-bounded candidate lines rendered into its
instructions in case C (§2.3.2).

**Invariant — no paraphrase.** Like the router, the searcher does not restate
the user's question. The synthesizer receives the raw message.

**Pause slot warning** (§9 trap 10). ``find_open_pause`` reads *the single open
pause per conversation*. The searcher and the deep_search planner share that one
slot, so a searcher pause must never be opened while a planner pause is live.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field
from pydantic_ai import (
    Agent,
    CallDeferred,
    DeferredToolRequests,
    ModelRetry,
    RunContext,
)
from pydantic_ai.usage import UsageLimits

# §7.2 / D13 — the article + regulation-title legs reuse fetch_article's PURE
# layer wholesale. Its deps Protocol has exactly one member (``.supabase``), so
# the resolver drops in unchanged, calibrated constants included.
from agents.tool_repository.fetch_article import (
    article_number_keys,
    resolve_regulation_id,
)
# 16,505 of 30,531 case summaries end in the pipeline's resolver-telemetry
# appendix and 252 in a Python traceback. The card's title is derived from the
# STRIPPED summary, so deriving ours from the raw one would title a card with a
# different string than the panel shows.
from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections
# §2.3.2 — the case-C preview. ``resolve_used_sources`` renders each ref exactly
# as its CARD does (doc_type chip, «حكم {court}» fallback, the 500-char cap);
# ``_fetch_used_refs`` is imported alongside it because those rendered lines
# carry no identity — only ``[n]`` — and the searcher needs the ids to hand on.
# Private-name import by necessity: the two must read the SAME rows in the same
# order, so re-implementing the fetch here would be the drift §2.3.2 warns about.
# ``_case_card_title`` / ``_circular_entity_name`` / ``_select_in`` join them for
# the identity join below, for the SAME reason: the card's title is derived by
# ``judgment_subject``, and a forked copy would title a published card
# differently from the page its «فتح في ريحان» button opens.
from agents.tool_repository.unfold_workspace_item import (
    _case_card_title,
    _circular_entity_name,
    _fetch_used_refs,
    _select_in,
    resolve_used_sources,
)
from agents.simple_search.models import (
    LEVEL_DOMAIN,
    LEVEL_REF_PREFIX,
    ResolvedObject,
    SimpleSearchLevel,
)
from agents.simple_search.prompts import (
    SEARCHER_SYSTEM_PROMPT,
    build_searcher_instructions,
)
from agents.utils.agent_models import ModelPolicy, get_agent_model

logger = logging.getLogger(__name__)

#: ``agents/utils/agent_models.py`` slot for this agent.
SEARCHER_SLOT = "simple_search_searcher"

#: The retrieval channel (C2's ``data_type`` vocabulary — kept byte-identical
#: so the manual-search tool and the searcher never disagree about the token).
SimpleSearchDataType = Literal[
    "regs", "judgments", "services", "circulars", "article"
]

#: D4 — at most three DISTINCT documents per turn. Enforced by the output
#: validator so an over-eager selection is a guided retry, not a silent trim.
MAX_FANOUT_DOCUMENTS = 3

#: §2.3.2 — the case-C preview is bounded ABOVE by what the card shows. 500 is
#: the card's own cap (``build_snippet``, ``preprocessor.py:364``); matching it
#: exactly makes parity provable, and the user cannot quote past it either.
PREVIEW_SNIPPET_CHARS = 500

# domain → level, inverted from the §6.1a wire contract so the two cannot drift.
_DOMAIN_LEVEL: dict[str, SimpleSearchLevel] = {
    domain: level for level, domain in LEVEL_DOMAIN.items()
}
# ref_id prefix → level, same discipline.
_PREFIX_LEVEL: dict[str, SimpleSearchLevel] = {
    prefix: level for level, prefix in LEVEL_REF_PREFIX.items()
}


# =========================================================================== #
# Deps.
# =========================================================================== #


@dataclass
class SearcherDeps:
    """Runtime deps for one searcher run.

    Built ONCE per turn (``runner.py``, before the cycle loop) — **not** per
    cycle, whatever an earlier draft of this line said. Load-bearing: handles
    accumulate across loop-back rounds, so a rejection note naming ``C1`` still
    names the same object on the next cycle. Re-minting per cycle would silently
    re-point every handle the notes refer to.

    ``candidates`` is the handle registry: the
    resolver tools mint ``C1``, ``C2``, … and the LLM selects handles rather
    than copying UUIDs out of a tool result — the same discipline as the
    ``WI-{n}`` aliases everywhere else in the tree, and for the same reason
    (a model that retypes a uuid eventually retypes it wrong).
    """

    supabase: Any
    user_id: str = ""
    conversation_id: str = ""
    case_id: str | None = None

    #: Handle → resolved object. Filled by the tools and by case-C prefetch.
    candidates: dict[str, ResolvedObject] = field(default_factory=dict)
    #: Rendered ``C1 — …`` preview lines for the instructions block (case C).
    candidate_lines: list[str] = field(default_factory=list)
    #: Rejection reasons from previous cycles this turn (D3 loop-back).
    rejection_notes: list[str] = field(default_factory=list)

    #: The conversation window — ``ChatMessageSnapshot`` rows from the SAME
    #: loader the planners use (``orchestrator._load_recent_messages``), so the
    #: provenance tags and the masking codec come along. The router does not
    #: paraphrase, so a follow-up («واللي بعدها؟») arrives with its referent
    #: only here.
    recent_messages: list = field(default_factory=list)

    #: Optional SSE sink, async (matches the runner's C1 signature).
    emit_sse: Callable[[dict], Any] | None = None

    def register_candidate(self, obj: ResolvedObject, preview: str = "") -> str:
        """Register a resolved object and return its handle (``C1``, ``C2``, …)."""
        handle = f"C{len(self.candidates) + 1}"
        self.candidates[handle] = obj
        if preview:
            self.candidate_lines.append(f"{handle} — {preview}")
        return handle


# =========================================================================== #
# Decision.
# =========================================================================== #


class SearcherDecision(BaseModel):
    """The searcher's output: identity, never content."""

    data_type: SimpleSearchDataType = Field(
        default="regs",
        description=(
            "Which retrieval channel this lookup belongs to. Decide it first "
            "and carry it with every downstream request."
        ),
    )
    selected: list[str] = Field(
        default_factory=list,
        description=(
            "Handles of the objects to hand on — the C1/C2/… labels the "
            "resolver tools returned, or the ones listed under the sources "
            "already in front of the user. Never a raw UUID."
        ),
    )
    objects: list[ResolvedObject] = Field(
        default_factory=list,
        description=(
            "Identities resolved OUTSIDE the handle registry (a manual-search "
            "hit, typically). Fill the level and the id fields exactly as the "
            "tool reported them. Prefer `selected` whenever a handle exists."
        ),
    )
    aborted: bool = Field(
        default=False,
        description=(
            "True when the turn is not a lookup at all. Set it in ANY of these:\n"
            "• Application, not identity («اتنفذت عليّ المادة 67 وصار لي…»).\n"
            "• **An INTEGRATIVE question across more than one document** — the "
            "answer is a relationship BETWEEN documents, not any one of them: "
            "«قارن الحكمين»، «وش الفرق بين حكم الابتدائية والاستئناف»، «وازن "
            "بينها وأيها أقوى سنداً»، «أيهما ينطبق على حالتي». This holds even "
            "when every document is perfectly resolvable — resolving them is "
            "not the problem. Each object you hand on is opened by a SEPARATE "
            "agent that never sees the others, so a comparison fanned out "
            "returns unrelated summaries side by side, answers nothing, and "
            "spends an unlock per ruling. Abort instead; the turn is re-routed "
            "to deep search, which holds them together.\n"
            "• Out of this family's scope for any other reason.\n"
            "**Two INDEPENDENT lookups in one message are NOT integrative** — "
            "«اش نظام العمل واش نظام التنفيذ» is two separate asks and should "
            "fan out normally. The test is whether the documents are asked "
            "about separately, or against each other."
        ),
    )
    abort_reason: str = Field(
        default="",
        description="Short Arabic reason. Empty unless aborted.",
    )
    rationale: str = Field(
        default="",
        description="Short Arabic note on how the object was resolved (logged).",
    )

    #: Validator-filled: ``selected`` handles + ``objects``, deduped, capped.
    resolved: list[ResolvedObject] = Field(
        default_factory=list,
        exclude=True,
        description=(
            "Do NOT fill this. It is populated by the output validator from "
            "`selected` and `objects`, and anything written here is overwritten."
        ),
    )


SEARCHER_LIMITS = UsageLimits(
    # Resolution + tool orchestration, not legal reasoning. Output is a handful
    # of ids; 16k is headroom for reasoning tokens (which count against
    # `output_tokens` on DashScope), not an expected size.
    output_tokens_limit=16_000,
    # Cumulative across pause/resume — the rehydrated message_history carries
    # the prior count forward. Covers: initial + a few resolver calls +
    # ask_user → pause → resume + output retries.
    request_limit=12,
    tool_calls_limit=10,
)


# =========================================================================== #
# Case C — the candidate list, built from what the USER sees (§2.3.1).
# =========================================================================== #


def identity_key(obj: ResolvedObject) -> str:
    """A dedup key covering EVERY identity an object can be opened by.

    ``ref_id()`` alone is not enough. An article resolved by
    ``(regulation_id, article_number)`` — the ``articles_v2``-miss path that
    falls to ``chunks_v2.owns`` — has no ``article_id``, so its ``ref_id()`` is
    ``""`` and its ``primary_id()`` is ``""``. Keying on those collapses «المادة
    5»، «المادة 7» and «المادة 81» of one نظام into a single object, which is
    precisely the D5 case (N articles of ONE law) the fan-out exists to serve.
    """
    if obj.ref_id():
        return obj.ref_id()
    return "|".join([
        obj.level, obj.regulation_id, obj.chunk_id, obj.article_id,
        obj.article_number, obj.case_id, obj.case_ref, obj.circular_id,
        obj.service_id,
    ])


def _identity_from_ref_row(row: dict) -> ResolvedObject | None:
    """Build a :class:`ResolvedObject` from one ``workspace_item_references`` row.

    The row carries ``item_id`` (the source row PK, migration 050) and
    ``ref_id`` (``reg:<chunks_v2.id>`` | ``case:<case_ref>`` | …). Both are
    tried, ``item_id`` first — except for judgments, where ``case_ref`` IS the
    key ``case:`` refs have always carried and what ``unfold_judgment`` queries
    on. Returns None for an unknown domain rather than guessing (§9 trap 5:
    an unknown domain silently drops the row on the read path too).
    """
    domain = (row.get("domain") or "").strip()
    level = _DOMAIN_LEVEL.get(domain)
    if level is None:
        logger.warning("simple_search searcher: unknown ref domain %r", domain)
        return None

    item_id = str(row.get("item_id") or "").strip()
    ref_id = str(row.get("ref_id") or "").strip()
    tail = ""
    if ref_id and ":" in ref_id:
        prefix, _, tail = ref_id.partition(":")
        # A prefix that names a DIFFERENT level than the domain is corruption,
        # not a fallback — drop the tail rather than mint a cross-wired id.
        if _PREFIX_LEVEL.get(prefix) not in (None, level):
            tail = ""

    obj = ResolvedObject(level=level)
    if level == "chunk":
        obj.chunk_id = item_id or tail
    elif level == "regulation_doc":
        obj.regulation_id = item_id or tail
    elif level == "article":
        obj.article_id = item_id or tail
    elif level == "judgment":
        obj.case_ref = tail
        obj.case_id = item_id
    elif level == "circular":
        obj.circular_id = item_id or tail
    elif level == "service":
        # compliance ref_ids carry a hash, not an id — item_id is load-bearing.
        obj.service_id = item_id
    return obj if not obj.missing_id() else None


# --------------------------------------------------------------------------- #
# The identity join (F3/F4) — parentage + the card's own display fields.
# --------------------------------------------------------------------------- #
#
# A ref row carries an id and a domain and nothing else. Two things downstream
# need more than that, and neither can recover it later:
#
# **F3 — parentage.** ``runner.document_key`` groups chunks and articles by their
# parent ``regulation_id`` (D5: N objects of ONE نظام → ONE synthesizer). The row
# has no ``regulation_id``, so without this join every chunk of one لائحة became
# its own document: the eval measured 3 chunks of ONE regulation fanning out into
# 3 synthesizers, 3 workspace items and 3 chat replies, burning the whole
# 3-document budget on one question.
#
# **F4 — the card's own title.** ``runner.build_references`` publishes
# ``obj.title``, which used to be the 500-char preview LINE — chip prefix,
# «## الملخص» markdown and all — so a Case-C answer republished its source with a
# manifest line for a label and an empty ``doc_type``: the chip that identified
# the ref on the way in was lost on the way out.
#
# One batched select per table, keyed exactly as the manifest resolvers key
# (``item_id`` first, ``ref_id`` tail as fallback), so the join and the preview
# line can never describe different rows.

_CHUNKS_TABLE = "chunks_v2"
_REGS_TABLE = "regulations_v2"
_ARTICLES_V2_TABLE = "articles_v2"
_CASES_TABLE = "cases"
_CIRCULARS_TABLE = "circulars"
_SERVICES_TABLE = "services"

#: ``regulations_v2.doc_type_raw``'s not-determined sentinel. Normalised away so
#: a published card is never chipped «غير محدد» — mirrors
#: ``unfold_workspace_item._reg_type_label`` / ``ura/enrich._doc_type_label``.
_DOC_TYPE_UNSPECIFIED = "غير محدد"


def _doc_type(raw: Any) -> str:
    """``doc_type_raw`` as the card's chip value — RAW, not the «نظام» fallback.

    ``Reference.doc_type`` is documented as ``regulations_v2.doc_type_raw`` and
    the panel itself does the fallback (``reference.doc_type?.trim() ||
    meta.label``), so writing «نظام» here would be writing a *rendered* value
    into a data field and would make a real نظام indistinguishable from an
    untyped document.
    """
    value = str(raw or "").strip()
    return "" if value == _DOC_TYPE_UNSPECIFIED else value


def _enrich_identities(supabase, objects: list[ResolvedObject]) -> None:
    """Fill parentage + display fields on case-C objects, in place.

    Batched per level; a level with no objects issues no query, and a row that
    cannot be joined is left exactly as it was (its preview line still stands, so
    the searcher can still match it — it just groups and publishes as itself).
    """
    by_level: dict[str, list[ResolvedObject]] = {}
    for obj in objects:
        by_level.setdefault(obj.level, []).append(obj)

    _enrich_chunks(supabase, by_level.get("chunk", []))
    _enrich_articles(supabase, by_level.get("article", []))
    _enrich_regulation_docs(supabase, by_level.get("regulation_doc", []))
    _enrich_judgments(supabase, by_level.get("judgment", []))
    _enrich_circulars(supabase, by_level.get("circular", []))
    _enrich_services(supabase, by_level.get("service", []))


def _regulation_display(supabase, reg_ids: list[str]) -> dict[str, dict]:
    """``regulations_v2.id`` → the parent row the card's chip and name come from."""
    rows = _select_in(
        supabase, _REGS_TABLE,
        "id, clean_title, title, doc_type_raw, landing_url", "id", reg_ids,
    )
    return {str(r["id"]): r for r in rows if r.get("id")}


def _apply_parent(obj: ResolvedObject, reg: dict | None) -> None:
    """Copy a parent regulation's display fields onto a child object."""
    if not reg:
        return
    obj.regulation_id = obj.regulation_id or str(reg.get("id") or "")
    obj.subtitle = obj.subtitle or str(
        reg.get("clean_title") or reg.get("title") or ""
    ).strip()
    obj.doc_type = obj.doc_type or _doc_type(reg.get("doc_type_raw"))
    obj.source_url = obj.source_url or str(reg.get("landing_url") or "").strip()


def _enrich_chunks(supabase, objects: list[ResolvedObject]) -> None:
    """L1 — chunk title + **the parent regulation id D5 groups on**."""
    if not objects:
        return
    rows = _select_in(
        supabase, _CHUNKS_TABLE, "id, title, regulation_id", "id",
        [o.chunk_id for o in objects],
    )
    chunk_by_id = {str(r["id"]): r for r in rows if r.get("id")}
    regs = _regulation_display(
        supabase, [str(r.get("regulation_id") or "") for r in rows]
    )
    for obj in objects:
        chunk = chunk_by_id.get(obj.chunk_id)
        if not chunk:
            continue
        obj.regulation_id = str(chunk.get("regulation_id") or "")
        obj.title = str(chunk.get("title") or "").strip()
        _apply_parent(obj, regs.get(obj.regulation_id))
        if not obj.title:
            obj.title = obj.subtitle or obj.label_ar()


def _enrich_articles(supabase, objects: list[ResolvedObject]) -> None:
    """L3 — ``articles_v2`` has no title column, so «المادة {n}» is the label.

    Same D5 payoff as chunks: three مواد of one نظام carry one ``regulation_id``
    and reach ONE synthesizer.
    """
    if not objects:
        return
    rows = _select_in(
        supabase, _ARTICLES_V2_TABLE, "id, article_number, regulation_id", "id",
        [o.article_id for o in objects if o.article_id],
    )
    art_by_id = {str(r["id"]): r for r in rows if r.get("id")}
    regs = _regulation_display(
        supabase,
        [str(r.get("regulation_id") or "") for r in rows]
        + [o.regulation_id for o in objects],
    )
    for obj in objects:
        art = art_by_id.get(obj.article_id)
        if art:
            obj.regulation_id = obj.regulation_id or str(art.get("regulation_id") or "")
            obj.article_number = obj.article_number or str(
                art.get("article_number") or ""
            ).strip()
        _apply_parent(obj, regs.get(obj.regulation_id))
        obj.title = (
            f"المادة {obj.article_number}" if obj.article_number else obj.label_ar()
        )


def _enrich_regulation_docs(supabase, objects: list[ResolvedObject]) -> None:
    """L2 — the whole نظام: its own name is the title, no parent above it."""
    if not objects:
        return
    regs = _regulation_display(supabase, [o.regulation_id for o in objects])
    for obj in objects:
        reg = regs.get(obj.regulation_id)
        if not reg:
            continue
        obj.title = str(reg.get("clean_title") or reg.get("title") or "").strip()
        obj.doc_type = _doc_type(reg.get("doc_type_raw"))
        obj.source_url = str(reg.get("landing_url") or "").strip()


def _enrich_judgments(supabase, objects: list[ResolvedObject]) -> None:
    """L4 — the panel's OWN derived title, «حكم {court}» fallback included.

    Also backfills ``case_id`` from ``case_ref`` where the ref row carried only
    the ref: the ledger keys on ``cases.id``, and having it in hand saves the
    unfold a lookup (it re-reads the row regardless — the charge must key on the
    row actually found, never on one carried in).
    """
    if not objects:
        return
    select = "id, case_ref, case_number, summary, short_summary, court, details_url"
    rows = _select_in(
        supabase, _CASES_TABLE, select, "id", [o.case_id for o in objects if o.case_id]
    )
    by_id = {str(r["id"]): r for r in rows if r.get("id")}
    missing = [o.case_ref for o in objects if not o.case_id and o.case_ref]
    by_ref = {}
    if missing:
        ref_rows = _select_in(supabase, _CASES_TABLE, select, "case_ref", missing)
        by_ref = {str(r["case_ref"]): r for r in ref_rows if r.get("case_ref")}

    for obj in objects:
        case = by_id.get(obj.case_id) or by_ref.get(obj.case_ref)
        if not case:
            continue
        obj.case_id = obj.case_id or str(case.get("id") or "")
        obj.case_ref = obj.case_ref or str(case.get("case_ref") or "")
        summary = strip_pipeline_sections(str(case.get("summary") or "").strip())
        obj.title = _case_card_title(case, summary)
        obj.subtitle = str(case.get("court") or "").strip()
        obj.source_url = str(case.get("details_url") or "").strip()


def _enrich_circulars(supabase, objects: list[ResolvedObject]) -> None:
    """L5 — title + issuing entity, the two strings the تعميم card prints."""
    if not objects:
        return
    rows = _select_in(
        supabase, _CIRCULARS_TABLE,
        "id, title, source, entities!circulars_entity_id_fkey(entity_name)", "id",
        [o.circular_id for o in objects],
    )
    by_id = {str(r["id"]): r for r in rows if r.get("id")}
    for obj in objects:
        circ = by_id.get(obj.circular_id)
        if not circ:
            continue
        obj.title = str(circ.get("title") or "").strip() or obj.label_ar()
        obj.subtitle = _circular_entity_name(circ)
        obj.source_url = str(circ.get("source") or "").strip()


def _enrich_services(supabase, objects: list[ResolvedObject]) -> None:
    """L6 — ``service_name_ar`` IS the whole card («{الجهة} - {اسم الخدمة}»).

    ``provider_name`` rides along for the ``regulation_title`` slot, which the
    house mapping fills with the provider for this domain
    (``preprocessor._reference_from_ura``).
    """
    if not objects:
        return
    rows = _select_in(
        supabase, _SERVICES_TABLE,
        "id, service_name_ar, provider_name, service_url, url", "id",
        [o.service_id for o in objects],
    )
    by_id = {str(r["id"]): r for r in rows if r.get("id")}
    for obj in objects:
        svc = by_id.get(obj.service_id)
        if not svc:
            continue
        obj.title = str(svc.get("service_name_ar") or "").strip() or obj.label_ar()
        obj.subtitle = str(svc.get("provider_name") or "").strip()
        obj.source_url = str(
            svc.get("service_url") or svc.get("url") or ""
        ).strip()


def collect_case_c_candidates(
    supabase, wi_ids: list[str], *, snippet_max_chars: int = PREVIEW_SNIPPET_CHARS
) -> list[tuple[ResolvedObject, str]]:
    """``unfold(preview)`` — the candidate refs of the attached workspace items.

    Pairs each used ref row with the card's own rendering of it
    (``resolve_used_sources``, whose formatters carry the ``doc_type`` chip and
    the «حكم {court}» title fallback the panel shows). Sync — the caller wraps
    it in ``asyncio.to_thread``.

    Two bounds hold at once, and both matter (§2.3.2):

    * *at least the card* — the searcher can match anything the user might
      quote, because the user names a source by what is printed in front of them;
    * *no more than the card* — every body-derived string is truncated to the
      card's snippet cap, so the searcher cannot spend budget reading objects it
      is about to discard.

    **The preview is the second half of the pair, never the object's title.**
    It is what the searcher MATCHES on; ``obj.title`` is what a published card is
    LABELLED with, and they are different strings for the same reason the manifest
    line is not a card: the line carries a chip prefix and «## الملخص» markdown.
    Assigning one to the other (which is what shipped) put a 500-char manifest
    line on every republished reference — see :func:`_enrich_identities`.
    """
    out: list[tuple[ResolvedObject, str]] = []
    for wi_id in wi_ids:
        if not wi_id:
            continue
        rows = _fetch_used_refs(supabase, wi_id)
        if not rows:
            continue
        lines = {
            line.n: line.text
            for line in resolve_used_sources(
                supabase, wi_id, snippet_max_chars=snippet_max_chars
            )
        }
        for row in rows:
            obj = _identity_from_ref_row(row)
            if obj is None:
                continue
            try:
                n = int(row.get("n") or 0)
            except (TypeError, ValueError):
                n = 0
            preview = lines.get(n) or obj.label_ar()
            # Prefix the ref's [n] — it is the number the USER's المراجع panel
            # prints on this exact source. Without it, «افتح المصدر رقم 3» has
            # nothing to match: the candidate lines carried only C-handles the
            # user never sees. (Found adversarially, 2026-08-16.)
            if n > 0:
                preview = f"[{n}] {preview}"
            out.append((obj, preview))

    # ONE batched join per level for the whole call, after every row is in hand —
    # not per WI, so two attached cards citing the same نظام cost one query.
    _enrich_identities(supabase, [obj for obj, _ in out])
    for obj, _ in out:
        if not obj.title:
            obj.title = obj.label_ar()
    return out


# =========================================================================== #
# Deterministic resolution — identity only, never a body.
# =========================================================================== #

_ARTICLES_TABLE = "articles_v2"


def fetch_article_identity(
    supabase, regulation_id: str, article_number: str
) -> dict[str, str]:
    """``{id, article_number}`` for one مادة — **identity, not content**.

    Deliberately does NOT select ``content``: the searcher hands off identity
    and the synthesizer's unfold reads the body. ``article_number`` is matched
    by exact TEXT equality — the corpus stores compound values («1-1»، «81 مكرر»)
    as strings and casting to int loses them. Never raises.

    Keys come from :func:`~agents.tool_repository.fetch_article.article_number_keys`,
    the SAME generator ``_fetch_article_content`` uses — raw first, then
    Arabic-Indic digits («٨١»), then the Arabic ordinal («الحادية والثمانون»).
    This was the tree's **third** copy of the article key and the only one on the
    runtime path: the eval measures ``_fetch_article_content``, so «٨١» read as
    fixed while ``resolve_article`` — the tool the searcher actually calls —
    still missed it. Sharing the generator is what stops the two drifting again.
    """
    for key in article_number_keys(str(article_number)):
        try:
            resp = (
                supabase.table(_ARTICLES_TABLE)
                .select("id, article_number")
                .eq("regulation_id", regulation_id)
                .eq("article_number", key)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "simple_search: article identity fetch failed reg=%s art=%r: %s",
                regulation_id, key, exc,
            )
            return {}
        data = getattr(resp, "data", None) or []
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        if row and row.get("id"):
            return {"id": str(row.get("id") or ""),
                    "article_number": str(row.get("article_number") or "")}
    # Miss shape is the pre-existing contract — empty STRINGS, not an empty
    # dict. Callers read .get("id"), so both would work, but silently changing
    # a return shape while fixing something else is how the next bug starts.
    return {"id": "", "article_number": ""}


def _register_manual_search(agent: Agent) -> bool:
    """Register the manual-search tool (C2). True when it was actually wired.

    The module is owned by a parallel lane. The import is lazy and guarded so
    this family stays importable — and testable — before that lane lands: a
    missing module degrades the searcher to its two deterministic resolvers
    plus ``ask_user``, which is exactly the right degradation (regulations and
    articles still resolve; judgments / services / circulars fall to
    ``ask_user`` instead of silently returning nothing).
    """
    try:
        from agents.simple_search.manual_search import register_manual_search
    except ImportError:
        logger.warning(
            "simple_search: manual_search not available — the searcher is "
            "running with deterministic resolvers only (judgments / services / "
            "circulars have no other path). See plan §12a C2."
        )
        return False
    register_manual_search(agent)
    return True


# =========================================================================== #
# Agent factory.
# =========================================================================== #


def create_searcher_agent(
    model_override: ModelPolicy | str | None = None,
) -> Agent[SearcherDeps, SearcherDecision | DeferredToolRequests]:
    """Build the searcher agent.

    A normal run emits a :class:`SearcherDecision`. When the LLM calls
    ``ask_user`` the run ends early with a :class:`DeferredToolRequests`; the
    caller records the pause and resumes via ``agent.run(message_history=…,
    deferred_tool_results=DeferredToolResults({tool_call_id: reply}))``.
    """
    agent: Agent[SearcherDeps, SearcherDecision | DeferredToolRequests] = Agent(
        get_agent_model(SEARCHER_SLOT, model_override),
        name="simple_search_searcher",
        deps_type=SearcherDeps,
        output_type=[SearcherDecision, DeferredToolRequests],
        instructions=SEARCHER_SYSTEM_PROMPT,
        retries=2,
        output_retries=3,
    )

    @agent.instructions
    def _per_turn(ctx: RunContext[SearcherDeps]) -> str:
        """Case-C candidates + any rejection feedback from an earlier cycle."""
        return build_searcher_instructions(ctx.deps)

    @agent.tool
    async def resolve_regulation(  # noqa: RUF029 — the supabase client is sync
        ctx: RunContext[SearcherDeps], regulation_title: str
    ) -> str:
        """Resolve a نظام / لائحة named by TITLE to a handle for the whole document.

        Use this when the user names a regulation and wants the document itself
        («اش نظام المنافسات والمشتريات؟»). Exact structured resolution — prefer
        it over search whenever the user named the thing.

        Returns:
            - ``"C1 — <resolved title>"`` on a confident match. Put that handle
              in ``selected``.
            - A string starting ``AMBIGUOUS:`` listing candidate titles when the
              name matches several regulations — use ``ask_user`` to ask which.
            - ``"لم يُعثر على نظام بهذا الاسم"`` when nothing matched — try
              manual search, or ``ask_user``.

        Args:
            regulation_title: the regulation as the user named it, e.g. «نظام العمل».
        """
        resolved = await asyncio.to_thread(
            resolve_regulation_id, ctx.deps.supabase, regulation_title
        )
        if resolved.ambiguous:
            return resolved.ambiguous
        if not resolved.reg_id:
            return "لم يُعثر على نظام بهذا الاسم"
        obj = ResolvedObject(
            level="regulation_doc",
            regulation_id=resolved.reg_id,
            title=resolved.display,
        )
        handle = ctx.deps.register_candidate(obj)
        note = "" if resolved.exact else " (تطابق تقريبي — تحقّق أنه النظام المقصود)"
        return f"{handle} — {resolved.display}{note}"

    @agent.tool
    async def resolve_article(  # noqa: RUF029 — the supabase client is sync
        ctx: RunContext[SearcherDeps],
        regulation_title: str,
        article_number: str,
    ) -> str:
        """Resolve ONE مادة, by number, inside a named نظام, to a handle.

        Use this when the user cites an article by its number («المادة 81 من
        نظام العمل»). Semantic search cannot retrieve an article by number — the
        corpus writes article numbers as Arabic words in the prose — so this
        deterministic lookup is the only reliable path.

        Pass ``article_number`` as the plain Western-digit string the user meant
        ("81", "1-1"); convert Arabic ordinals («الحادية والثمانون») or
        Arabic-Indic digits («٨١») first.

        Returns:
            - ``"C1 — المادة 81 من نظام العمل"`` on success.
            - ``AMBIGUOUS: …`` when the regulation name matches several — use
              ``ask_user``.
            - ``"لم يُعثر على المادة …"`` when the نظام or the article is not
              found. The article may still exist under a chunk that owns it, so
              hand on the نظام + the number as an `article` identity and let the
              opening step try its fallback, or ``ask_user``.

        Args:
            regulation_title: the regulation as the user named it.
            article_number: the article number as an exact-text key.
        """
        num = (article_number or "").strip()
        resolved = await asyncio.to_thread(
            resolve_regulation_id, ctx.deps.supabase, regulation_title
        )
        if resolved.ambiguous:
            return resolved.ambiguous
        if not resolved.reg_id:
            return f"لم يُعثر على المادة {num}: النظام «{regulation_title.strip()}» غير موجود"

        identity = await asyncio.to_thread(
            fetch_article_identity, ctx.deps.supabase, resolved.reg_id, num
        )
        obj = ResolvedObject(
            level="article",
            regulation_id=resolved.reg_id,
            article_id=identity.get("id", ""),
            # Always carry the number: it is the second identity the unfold
            # accepts, and the ONLY key its chunks_v2.owns fallback can use.
            article_number=identity.get("article_number") or num,
            title=f"المادة {num}",
            subtitle=resolved.display,
        )
        handle = ctx.deps.register_candidate(obj)
        if not identity.get("id"):
            return (
                f"{handle} — المادة {num} من {resolved.display} "
                "(غير موجودة في جدول المواد؛ سيُحاول استخراجها من المقطع الذي يضمّها)"
            )
        return f"{handle} — المادة {num} من {resolved.display}"

    @agent.tool_plain
    async def ask_user(question: str) -> str:  # noqa: RUF029
        """Ask ONE clarifying question, pausing the turn until the user replies.

        Use it when resolution is genuinely ambiguous and guessing would cost
        the user a whole turn:

        1. A ``resolve_*`` tool came back ``AMBIGUOUS:`` — two or more
           regulations plausibly match the name they used.
        2. Two sources already in front of the user both match what they said.
        3. The object cannot be found at all and you need the title, the number,
           or the court to try again.

        Ask about the OBJECT's identity only. Do not ask what they want to do
        with it, and do not ask a question you could answer by resolving.

        When raised, the run terminates with a ``DeferredToolRequests``.

        Args:
            question: one concise Arabic question.

        Returns:
            The user's reply (delivered on resume).
        """
        raise CallDeferred

    @agent.output_validator
    def _resolve_handles(
        ctx: RunContext[SearcherDeps], value: SearcherDecision
    ) -> SearcherDecision:
        """Resolve handles → objects, dedupe, and enforce the D4 fan-out cap.

        An unknown handle raises ``ModelRetry`` with an Arabic message so the
        searcher self-corrects — the same guided-retry shape as the router's
        ``WI-{n}`` alias resolver. A hand-filled ``objects`` entry that carries
        no usable id is rejected the same way: a half-resolved object would
        reach the unfold and come back empty, which reads as "not found" when it
        is really "never resolved".
        """
        if value.aborted:
            value.resolved = []
            return value

        resolved: list[ResolvedObject] = []
        for handle in value.selected:
            key = (handle or "").strip().upper()
            obj = ctx.deps.candidates.get(key)
            if obj is None:
                raise ModelRetry(
                    f"المُعرّف {handle!r} غير معروف. استخدم أحد المعرّفات التي "
                    f"أعادتها الأدوات: {sorted(ctx.deps.candidates) or 'لا يوجد'}."
                )
            resolved.append(obj)

        for obj in value.objects:
            missing = obj.missing_id()
            if missing:
                raise ModelRetry(
                    f"الكائن من نوع {obj.level!r} ينقصه المعرّف {missing!r}. "
                    "أعد الحقل كما أعادته الأداة، أو استخدم أداة الحل المناسبة."
                )
            resolved.append(obj)

        if not resolved:
            raise ModelRetry(
                "لم تحدّد أي كائن. استخدم أدوات الحل ثم ضع المعرّفات في "
                "`selected`، أو اضبط `aborted` إن كان الطلب خارج نطاق البحث المباشر."
            )

        # Dedupe on the ref key so the same object selected twice (a handle AND
        # a hand-filled copy) does not fan out into two synthesizers.
        seen: set[str] = set()
        unique: list[ResolvedObject] = []
        for obj in resolved:
            key = identity_key(obj)
            if key in seen:
                continue
            seen.add(key)
            unique.append(obj)

        if len(unique) > MAX_FANOUT_DOCUMENTS:
            raise ModelRetry(
                f"حدّدت {len(unique)} كائنًا؛ الحد الأقصى "
                f"{MAX_FANOUT_DOCUMENTS} مستندات في الدور الواحد. "
                "اختر الأكثر مركزية لسؤال المستخدم."
            )

        value.resolved = unique
        return value

    # C2 — the searcher CALLS the registration entry point; it does not
    # implement the tool. Owned by the manual-search lane.
    _register_manual_search(agent)

    return agent


__all__ = [
    "SEARCHER_SLOT",
    "SEARCHER_LIMITS",
    "MAX_FANOUT_DOCUMENTS",
    "PREVIEW_SNIPPET_CHARS",
    "SimpleSearchDataType",
    "SearcherDeps",
    "SearcherDecision",
    "collect_case_c_candidates",
    "fetch_article_identity",
    "identity_key",
    "create_searcher_agent",
]
