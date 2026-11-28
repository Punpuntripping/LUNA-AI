"""«بحث شامل» — run a plan's searches in parallel (plan §4.5; build spec "B").

Each request of the chosen option is one complete deep search through the production
retrieval path (``build_retrieval_config`` → ``run_retrieval``: sector picker,
expanders, rerankers, aggregator) on its OWN ``PlannerDeps`` — ``run_retrieval``
writes its results onto the deps it is given, so N searches need N deps
(``deps_factory``). What each search receives (D17, D29, D22, §4.9 b):

- the user's message word for word (``query``) and the conversation before it (on the
  deps, as today);
- every document of the angle map WHOLE, with its code-written section markers
  (``DocView.marked_text``) and its summary — through a whole-file hook in
  ``deep_search_v4/orchestrator.py`` (``deps._case_doc_caps``: 200k per document /
  300k per search, no segmented section selection, no ``doc_type`` filter);
  ``case_full_text_wis`` and ``reg_full_text_wis`` both = every document;
- ``planner_brief`` = :func:`brief.build_planner_brief` (§4.9 b — the request's angles,
  focus and the search instruction, labelled as analysis);
- ``mode`` from the request, never ``support`` (D21);
- the request's ``max_sub_queries`` as the expanders' query cap (``None`` = uncapped).

Distinct query ids (in-app ``query_id`` is 0 → the phase log ids would collide on
the same second). All searches run at once (``asyncio.gather``); one failure never
sinks the others. Progress: ONE combined ``agent_progress`` stream (stage = the
least-advanced running search's, never past ``aggregating``; ``sources`` = the
running sum over every search; ``data.searches = {total, done}``) — the first search
to finish never moves the bar to "writing".

وضع السرية: the deps' ``attached_items`` / ``recent_messages`` are the turn's encoded
copies (the wiring builds them as ``_run_deep_search`` does); the view's text (marked
document text, titles, summaries) and the brief are encoded here with the turn codec,
and the new fakes are persisted before any model runs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from agents.models import WorkspaceItemSnapshot
from agents.utils.tracking import track_stage

from .models import ExtendedPlan, ExtendedRequest, ExtendedSearchReport, PlannerView
from .view import file_tagged_text, whole_file_label

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agents.deep_search_v4.planner.deps import PlannerDeps

logger = logging.getLogger(__name__)

#: Q11 default (build spec): every document whole up to this many characters…
WHOLE_FILE_PER_DOC_CAP = 200_000
#: …and this many per search (per recipient); beyond that, today's cut + note.
WHOLE_FILE_TOTAL_CAP = 300_000

#: ``ExtendedSearchReport.error`` codes for a search that ran but produced nothing usable.
ERROR_NO_REFERENCES = "no_references"
ERROR_AGGREGATOR_FAILED = "aggregator_failed"

# Combined progress — static Arabic labels only (progress text is not decoded on the
# way to the client, so it never carries model or user text).
_STAGE_ORDER = {"searching": 1, "evaluating": 2, "aggregating": 3}
_STAGE_TEXT_AR = {
    "searching": "البحث في الأنظمة والأحكام",
    "evaluating": "تقييم النتائج وترجيحها",
    "aggregating": "كتابة تقارير البحوث وتوثيق المراجع",
}
_START_TEXT_AR = "بدء البحوث المتوازية"
_ALL_DONE_MERGE_TEXT_AR = "اكتملت البحوث — دمج النتائج في جواب واحد"
_ALL_DONE_TEXT_AR = "اكتملت البحوث — إعداد الجواب"


# --------------------------------------------------------------------------- documents


def _encode(text: str) -> str:
    """The turn codec's encode (passthrough outside a masked turn)."""
    from backend.app.services.masking_service import encode_active

    return encode_active(text or "") if text else (text or "")


def whole_file_items(
    view: PlannerView,
    attached_items: Sequence[WorkspaceItemSnapshot] | None = None,
    wi_alias_map: dict[int, str] | None = None,
    *,
    file_tags: bool = False,
) -> list[WorkspaceItemSnapshot]:
    """The case documents every search reads whole — encoded snapshots, map order.

    One snapshot per document of the view: its ``marked_text`` (encoded) as
    ``content_md`` — or, when the view has no marked text, the attached snapshot's
    own (already encoded) ``content_md``; the attached snapshot's summary / title
    (already encoded) or the view's (encoded here); ``wi_seq`` from the attached
    snapshot, else the conversation's alias map, else a free number after every
    known one. Then every other attached attachment of the turn as it is (D29: each
    document of the turn travels). Never raises on one bad document — it is skipped.

    ``file_tags`` (the file refs on, aggregator_file_refs.md): each marker opens with the
    file's tag (〔F3·S7 · …〕, F + its ``wi_seq``) instead of the map's 〔D1·S7 · …〕, and a
    document with no marked sections gets one label over its text (〔F3〕). Off = today.
    """
    attached = [s for s in attached_items or [] if getattr(s, "kind", None) == "attachment"]
    by_id = {str(s.item_id): s for s in attached}
    seq_of_item = {str(v): int(k) for k, v in (wi_alias_map or {}).items()}
    known = {s.wi_seq for s in attached if s.wi_seq is not None} | set(seq_of_item.values())
    next_free = max(known, default=0) + 1

    out: list[WorkspaceItemSnapshot] = []
    for doc in view.documents:
        try:
            base = by_id.get(str(doc.item_id))
            seq = base.wi_seq if base is not None and base.wi_seq is not None else seq_of_item.get(str(doc.item_id))
            if seq is None:
                seq, next_free = next_free, next_free + 1
            if (doc.marked_text or "").strip():
                text = file_tagged_text(doc.marked_text, doc.id, int(seq)) if file_tags else doc.marked_text
                content = _encode(text)
            else:
                content = base.content_md if base is not None else ""
                if file_tags and (content or "").strip():
                    content = f"{whole_file_label(int(seq))}\n\n{content}"
            summary = (base.summary if base is not None and base.summary else _encode(doc.summary)) or ""
            title = base.title if base is not None and base.title else _encode(doc.title)
            metadata = dict(base.metadata) if base is not None and base.metadata else {}
            if doc.doc_type and not metadata.get("doc_type"):
                metadata["doc_type"] = doc.doc_type
            out.append(WorkspaceItemSnapshot(
                item_id=str(doc.item_id),
                kind="attachment",
                title=title or "",
                content_md=content or "",
                summary=summary,
                metadata=metadata,
                wi_seq=int(seq),
            ))
        except Exception:  # noqa: BLE001 - one bad document never sinks the searches
            logger.warning("extended_search: document %s skipped", doc.id, exc_info=True)
    in_view = {s.item_id for s in out}
    for s in attached:
        if str(s.item_id) in in_view or s.wi_seq is None:
            continue
        if file_tags and (s.content_md or "").strip():
            s = s.model_copy(update={"content_md": f"{whole_file_label(int(s.wi_seq))}\n\n{s.content_md}"})
        out.append(s)
    return out


def whole_file_refs(view: PlannerView, items: Sequence[WorkspaceItemSnapshot]) -> list[Any]:
    """What each search may cite (``FileRefDoc``, aggregator_file_refs.md §4.1): every
    document of ``items`` with a WI number and text — its marked sections
    (``DocView.section_spans``), or none when it travels whole (cited ``[F3]``)."""
    from agents.deep_search_v4.shared.case_documents import FileRefDoc

    spans = {str(d.item_id): tuple(d.section_spans) for d in view.documents if (d.marked_text or "").strip()}
    out: list[Any] = []
    for s in items:
        if s.wi_seq is None or int(s.wi_seq) <= 0 or not (s.content_md or "").strip():
            continue
        out.append(FileRefDoc(wi_seq=int(s.wi_seq), item_id=str(s.item_id), sections=spans.get(str(s.item_id), ())))
    return out


def file_wi_of(view: PlannerView, items: Sequence[WorkspaceItemSnapshot]) -> dict[str, int]:
    """The map's document id → the file's WI number (``{"D1": 3}``) — how the briefs
    write a section once the markers carry file tags."""
    seq = {str(s.item_id): int(s.wi_seq) for s in items if s.wi_seq is not None}
    return {d.id: seq[str(d.item_id)] for d in view.documents if str(d.item_id) in seq}


def _file_tags_on(deps: Any) -> bool:
    """The file refs are on for this user (the same switch as the ordinary deep search)."""
    from agents.utils.aggregator_file_ref_flags import file_refs_enabled

    return file_refs_enabled(getattr(deps, "user_id", None))


def _persist_fakes(deps: Any) -> None:
    """Flush the fakes minted while encoding, before any model reads them."""
    try:
        from backend.app.services.masking_service import active_codec, persist_new_mappings

        persist_new_mappings(deps.supabase, deps.user_id, active_codec())
    except Exception:  # noqa: BLE001 - masking telemetry must not stop the searches
        logger.warning("extended_search: persisting new masking fakes failed", exc_info=True)


def _brief(
    request: ExtendedRequest, plan: ExtendedPlan, view: PlannerView, file_wi: dict[str, int] | None = None,
) -> str:
    """§4.9 b — built by code (A's ``brief`` module), encoded with the turn codec."""
    from .brief import build_planner_brief

    return _encode(build_planner_brief(request, plan=plan, view=view, file_wi=file_wi))


def _base_query_id() -> int:
    """A per-run base for the searches' query ids (query id = base + index)."""
    return int(time.time()) % 1_000_000 * 100


# --------------------------------------------------------------------------- progress


class CombinedProgress:
    """Folds N searches' ``agent_progress`` events into ONE stream on ``emit``.

    Per search: its own stage (monotonic: searching → evaluating → aggregating) and
    the results its executor phases kept. Combined: the least-advanced stage among
    the searches still running (``aggregating`` once all are done), the sum of every
    search's results, ``searches = {total, done}``. An event is emitted only when
    one of the three changes. Executor topic lines (``type == "status"``) pass
    through unchanged. Never raises.
    """

    def __init__(self, total: int, emit: Callable[[dict], None] | None) -> None:
        self.total = total
        self._emit = emit
        self._stage: dict[int, str] = {i: "searching" for i in range(1, total + 1)}
        self._sources: dict[int, int] = {i: 0 for i in range(1, total + 1)}
        self._done: set[int] = set()
        self._last: tuple | None = None

    # -- read
    @property
    def sources(self) -> int:
        return sum(self._sources.values())

    @property
    def done(self) -> int:
        return len(self._done)

    def stage(self) -> str:
        running = [s for i, s in self._stage.items() if i not in self._done]
        if not running:
            return "aggregating"
        return min(running, key=lambda s: _STAGE_ORDER[s])

    def sources_of(self, index: int) -> int:
        return self._sources.get(index, 0)

    # -- write
    def sink(self, index: int) -> Callable[[dict], None]:
        """The ``emit_sse`` of search ``index`` (1-based)."""

        def _sink(event: dict) -> None:
            try:
                self._on_event(index, event)
            except Exception:  # noqa: BLE001 - a broken progress sink never perturbs a search
                logger.debug("extended_search: progress event dropped", exc_info=True)

        return _sink

    def start(self) -> None:
        self._publish(_START_TEXT_AR, force=True)

    def finish(self, index: int) -> None:
        self._done.add(index)
        if len(self._done) < self.total:
            self._publish()

    def all_done(self, *, merging: bool) -> None:
        self._done.update(self._stage)
        self._publish(_ALL_DONE_MERGE_TEXT_AR if merging else _ALL_DONE_TEXT_AR, force=True)

    def _on_event(self, index: int, event: dict) -> None:
        if not isinstance(event, dict):
            return
        if event.get("type") == "status":
            self._forward(event)
            return
        if event.get("type") != "agent_progress" or index in self._done:
            return
        stage = str(event.get("stage") or "")
        data = event.get("data") or {}
        if stage == "searching" and data.get("phase"):
            try:
                self._sources[index] = self._sources.get(index, 0) + int(data.get("sources") or 0)
            except (TypeError, ValueError):
                pass
        if stage in _STAGE_ORDER and _STAGE_ORDER[stage] > _STAGE_ORDER[self._stage[index]]:
            self._stage[index] = stage
        self._publish()

    def _publish(self, text: str | None = None, *, force: bool = False) -> None:
        stage = self.stage()
        key = (stage, self.sources, self.done)
        if key == self._last and not force:
            return
        self._last = key
        self._forward({
            "type": "agent_progress",
            "stage": stage,
            "text": text or _STAGE_TEXT_AR[stage],
            "data": {
                "sources": self.sources,
                "searches": {"total": self.total, "done": self.done},
            },
        })

    def _forward(self, event: dict) -> None:
        if self._emit is None:
            return
        try:
            self._emit(event)
        except Exception:  # noqa: BLE001
            logger.debug("extended_search: emit_sse failed", exc_info=True)


# --------------------------------------------------------------------------- one search


def _usable(agg_output: Any) -> str:
    """``""`` when the aggregator output is a real report, else the error code."""
    from agents.deep_search_v4.aggregator.runner import _MODEL_FAILURE_GAP_AR

    gaps = [str(g) for g in (getattr(agg_output, "gaps", None) or [])]
    if _MODEL_FAILURE_GAP_AR in gaps:
        return ERROR_AGGREGATOR_FAILED
    if not (getattr(agg_output, "references", None) or []):
        return ERROR_NO_REFERENCES
    if not (getattr(agg_output, "synthesis_md", "") or "").strip():
        return ERROR_AGGREGATOR_FAILED
    return ""


async def _run_one(
    index: int,
    request: ExtendedRequest,
    *,
    deps: Any,
    items: list[WorkspaceItemSnapshot],
    brief: str,
    query: str,
    query_id: int,
    total: int,
    progress: CombinedProgress,
    file_ref_docs: Sequence[Any] = (),
) -> ExtendedSearchReport:
    """One search on its own deps. Never raises (except cancellation). ``file_ref_docs``
    — what its aggregator may cite (the file refs on); empty = no file tags."""
    from agents.deep_search_v4.orchestrator import run_retrieval
    from agents.deep_search_v4.planner.apply import build_retrieval_config
    from agents.deep_search_v4.planner.models import PlannerDecision

    t0 = time.perf_counter()
    report = ExtendedSearchReport(
        index=index, request=request, ok=False, query_id=query_id,
        documents=list(items), file_ref_docs=list(file_ref_docs),
    )
    with track_stage(
        "deep_search.extended.search",
        conversation_id=getattr(deps, "conversation_id", "") or None,
        agent_family="deep_search",
        index=index,
        of=total,
        mode=request.mode,
        cap=request.max_sub_queries or 0,
        query_id=query_id,
        documents=len(items),
    ) as span:
        try:
            if not getattr(deps, "query_id", 0):
                deps.query_id = query_id
            report.query_id = int(getattr(deps, "query_id", 0) or query_id)
            deps.emit_sse = progress.sink(index)
            deps.attached_items = list(items)
            deps._case_doc_caps = (WHOLE_FILE_PER_DOC_CAP, WHOLE_FILE_TOTAL_CAP)
            if file_ref_docs:
                # The orchestrator numbers the aggregator's documents from these
                # (``_build_case_document_lists``): the tags its labels carry.
                deps._whole_file_refs = list(file_ref_docs)
            aliases = [f"WI-{s.wi_seq}" for s in items]
            decision = PlannerDecision(
                mode=request.mode,
                support=False,
                rationale=f"extended_search {index}/{total}",
                planner_brief=brief,
                case_full_text_wis=aliases,
                reg_full_text_wis=aliases,
            )
            deps._decision = decision
            config = build_retrieval_config(decision, cap=request.max_sub_queries)
            agg_output = await run_retrieval(query, config, deps)
            report.agg_output = agg_output
            report.error = _usable(agg_output)
            report.ok = not report.error
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one failed search never sinks the others
            logger.error("extended_search: search %d/%d failed: %s", index, total, exc, exc_info=True)
            report.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            report.seconds = round(time.perf_counter() - t0, 1)
            report.ura = getattr(deps, "_ura", None)
            report.reg_rqrs = list(getattr(deps, "_reg_rqrs", None) or [])
            report.case_rqrs = list(getattr(deps, "_case_rqrs", None) or [])
            report.per_executor_stats = dict(getattr(deps, "_per_executor_stats", None) or {})
            report.sources = progress.sources_of(index)
            progress.finish(index)
        try:
            span.set(
                ok=report.ok,
                error=report.error[:200],
                seconds=report.seconds,
                sources=report.sources,
                references=len(getattr(report.agg_output, "references", None) or []),
                confidence=getattr(report.agg_output, "confidence", None) or "",
            )
            if not report.ok:
                span.set_outcome("error")
        except Exception:  # noqa: BLE001 - the no-op handle has no set_outcome
            pass
    return report


# --------------------------------------------------------------------------- entry point


async def run_extended_searches(
    requests: Sequence[ExtendedRequest],
    *,
    plan: ExtendedPlan,
    view: PlannerView,
    query: str,
    deps_factory: Callable[[int], "PlannerDeps"],
    emit_sse: Callable[[dict], None] | None = None,
    on_report: Callable[[ExtendedSearchReport], Awaitable[None]] | None = None,
) -> list[ExtendedSearchReport]:
    """Run ``requests`` in parallel; one report per request, plan order.

    ``deps_factory(index)`` (1-based, = ``ExtendedSearchReport.index``) returns a FRESH
    ``PlannerDeps`` for that search, built like ``_run_deep_search`` builds the turn's
    (encoded ``attached_items`` / ``recent_messages``, ``user_id``, ``conversation_id``,
    ``http_client``…). The runner sets on it: ``query_id`` (when 0), ``emit_sse`` (its
    slot of the combined stream), ``attached_items`` (the whole-file documents),
    ``_case_doc_caps``, ``_decision``. ``query`` is the message the searches run on,
    as the turn received it (encoded). ``emit_sse`` gets the combined progress stream
    and the executors' topic lines. ``on_report`` is awaited with each report the moment
    its search ends (the wiring posts it to the workspace then — owner 2026-11-27); it
    never fails the search.
    """
    reqs = list(requests)
    total = len(reqs)
    progress = CombinedProgress(total, emit_sse)
    if not total:
        return []
    progress.start()

    # Deps first (sync) — a factory failure fails that search only.
    deps_list: list[Any] = []
    for i in range(1, total + 1):
        try:
            deps_list.append(deps_factory(i))
        except Exception as exc:  # noqa: BLE001
            logger.error("extended_search: deps_factory(%d) failed: %s", i, exc, exc_info=True)
            deps_list.append(exc)
    ref = next((d for d in deps_list if not isinstance(d, BaseException)), None)

    # The documents and the briefs, encoded once; fakes persisted before any model runs.
    # With the file refs on, the markers and the briefs carry the file tags (F + WI no.).
    tags = ref is not None and _file_tags_on(ref)
    items: list[WorkspaceItemSnapshot] = []
    if ref is not None:
        items = whole_file_items(
            view,
            getattr(ref, "attached_items", None) or [],
            getattr(ref, "wi_alias_map", None) or {},
            file_tags=tags,
        )
    file_ref_docs = whole_file_refs(view, items) if tags else []
    file_wi = file_wi_of(view, items) if tags else None
    briefs: list[str | BaseException] = []
    for req in reqs:
        try:
            briefs.append(_brief(req, plan, view, file_wi))
        except Exception as exc:  # noqa: BLE001 - that search fails, the others run
            logger.error("extended_search: brief for %r failed: %s", req.title, exc, exc_info=True)
            briefs.append(exc)
    if ref is not None:
        _persist_fakes(ref)

    base_qid = _base_query_id()
    logger.info(
        "extended_search: %d searches — modes=%s caps=%s documents=%d file_refs=%d",
        total, [r.mode for r in reqs], [r.max_sub_queries for r in reqs], len(items), len(file_ref_docs),
    )

    async def _one(i: int, req: ExtendedRequest) -> ExtendedSearchReport:
        deps, brief = deps_list[i - 1], briefs[i - 1]
        if isinstance(deps, BaseException) or isinstance(brief, BaseException):
            exc = deps if isinstance(deps, BaseException) else brief
            progress.finish(i)
            return ExtendedSearchReport(
                index=i, request=req, ok=False, error=f"{type(exc).__name__}: {exc}"[:500],
                documents=list(items), file_ref_docs=list(file_ref_docs),
            )
        report = await _run_one(
            i, req, deps=deps, items=items, brief=brief, query=query,
            query_id=base_qid + i, total=total, progress=progress, file_ref_docs=file_ref_docs,
        )
        if on_report is not None and report.ok:
            try:
                await on_report(report)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - posting early is a bonus; the end posts it anyway
                logger.warning("extended_search: on_report(%d) failed", i, exc_info=True)
        return report

    reports = list(await asyncio.gather(*(_one(i, r) for i, r in enumerate(reqs, 1))))
    ok = sum(1 for r in reports if r.ok)
    progress.all_done(merging=ok >= 2)
    logger.info(
        "extended_search: %d/%d searches ok — %s",
        ok, total, [(r.index, r.ok, r.error[:40], r.seconds) for r in reports],
    )
    return reports


__all__ = [
    "CombinedProgress",
    "ERROR_AGGREGATOR_FAILED",
    "ERROR_NO_REFERENCES",
    "WHOLE_FILE_PER_DOC_CAP",
    "WHOLE_FILE_TOTAL_CAP",
    "file_wi_of",
    "run_extended_searches",
    "whole_file_items",
    "whole_file_refs",
]
