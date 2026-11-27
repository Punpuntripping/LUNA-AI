"""Publish the extended-search merger's answer («بحث شامل») as ONE workspace item.

The sibling of ``agents/agent_search/publisher.py`` — an adapter, not an LLM.
Plan: ``.claude/plans/extended_search.md`` §4.7, D14.

Owner decisions (2026-11-24) this module encodes:

* **A subtype, not a kind.** The row is ``kind='agent_search'`` with
  ``metadata.subtype = "extended_synthesis"``, so every consumer of
  ``agent_search`` (writer sources, router context, references panel, mobile)
  keeps working without a DB enum value or a cap rule.
* **The children stay ordinary items.** Each search's report was already
  published as its own ``agent_search`` item; this item only LISTS them —
  ``metadata.children`` is the hook for the workspace-item relationships plan.
  Nothing here writes to a child row.
* **``content_md`` is the answer only.** The scoreboard, the settled
  contradictions and the angles not searched travel as structured metadata for
  the UI to render later.

Store-real invariant (وضع السرية): the merger's text is pipeline text, i.e.
masked fakes on a masked turn. Every free-text string that reaches the row —
the title, ``content_md``, ``describe_query`` and every text field inside the
metadata — goes through ``decode_for_persist``. Ids, enums, numbers and
booleans are never decoded (a UUID or a count is not text a codec may touch).

No ``retrieval_artifacts`` / ``reranker_runs``: they belong to each child
report's item, which owns the search that produced them.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from agents.agent_search.deps import SearchPublishDeps
from agents.agent_search.models import SearchPublishOutput
from agents.deep_search_v4.extended_merger.models import (
    SUBTYPE,
    MergedPublishInput,
    MergerOutput,
)
from agents.utils.tracking import track_stage
from backend.app.services.masking_service import decode_for_persist
from backend.app.services.references_service import persist_item_references
from backend.app.services.workspace_service import create_workspace_item

_KIND = "agent_search"
_CREATED_BY = "agent"
_AGENT_FAMILY = "deep_search"
_FALLBACK_TITLE = "بحث شامل"
_TITLE_MAX = 80

# The aggregator's cited-count scan (``aggregator/artifact_builder.py``):
# ``[n]`` and ``[n,m]`` tokens in the body.
_CITE_RE = re.compile(r"\[([\d,\s]+)\]")


def _decode(text: Any) -> str:
    """``decode_for_persist`` on a free-text value; ``None`` → ``""``."""
    return decode_for_persist(str(text or ""))


def _build_title(input: MergedPublishInput) -> str:
    """Router label → the user's query → «بحث شامل». Decoded BEFORE the cut,
    so a fake is never sliced in half and left undecodable."""
    for candidate in (input.task_label, input.original_query):
        text = _decode((candidate or "").strip()).strip()
        if text:
            return text[:_TITLE_MAX]
    return _FALLBACK_TITLE


def _body(output: MergerOutput) -> str:
    return (output.synthesis_md or "").rstrip()


def _build_content_md(output: MergerOutput) -> str:
    """The merged answer; the disclaimer is appended only when one is set
    (mirrors ``artifact_builder.build_artifact`` — migration 074 moved it to
    the UI, so it is normally empty)."""
    content = _body(output)
    disclaimer = (output.disclaimer_ar or "").strip()
    if disclaimer:
        content = content + "\n\n---\n\n" + disclaimer
    return _decode(content)


def _cited_numbers(output: MergerOutput) -> list[int]:
    """Distinct ``[n]`` numbers in the body that are reference numbers."""
    ref_numbers = {ref.n for ref in output.references or []}
    body = _body(output)
    cited: set[int] = set()
    if body and ref_numbers:
        for match in _CITE_RE.finditer(body):
            for tok in match.group(1).split(","):
                tok = tok.strip()
                if tok.isdigit() and int(tok) in ref_numbers:
                    cited.add(int(tok))
    return sorted(cited)


def _children(output: MergerOutput) -> list[str]:
    """Item ids of the reports that were published, report order, no repeats."""
    seen: list[str] = []
    for report in output.reports:
        if report.item_id and report.item_id not in seen:
            seen.append(report.item_id)
    return seen


def _json_safe(value: Any) -> Any:
    """``stats`` is ``dict[str, Any]`` — never let an odd value fail the insert."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _build_metadata(input: MergedPublishInput, cited: list[int]) -> dict:
    out = input.output
    reports = []
    for r in out.reports:
        entry = r.model_dump(mode="json")
        entry["title"] = _decode(r.title)
        reports.append(entry)
    angles = []
    for a in out.angles:
        entry = a.model_dump(mode="json")
        for key in ("title", "for_party", "argument", "legal_question", "weakness"):
            entry[key] = _decode(getattr(a, key))
        entry["sections"] = [_decode(s) for s in a.sections]
        angles.append(entry)
    angle_status = []
    for s in out.angle_status:
        entry = s.model_dump(mode="json")
        entry["note"] = _decode(s.note)
        angle_status.append(entry)
    resolved = []
    for res in out.resolved:
        entry = res.model_dump(mode="json")
        entry["topic"] = _decode(res.topic)
        entry["decision"] = _decode(res.decision)
        resolved.append(entry)
    left_out = []
    for lo in out.left_out:
        entry = lo.model_dump(mode="json")
        entry["title"] = _decode(lo.title)
        entry["why"] = _decode(lo.why)
        left_out.append(entry)
    gaps = [_decode(g.strip()) for g in out.gaps if str(g or "").strip()]

    return {
        "subtype": SUBTYPE,
        "confidence": out.confidence,
        "detail_level": input.detail_level,
        "client_side": input.client_side,
        "run_id": input.run_id,
        "prompt_version": out.prompt_version,
        "model_used": out.model_used,
        "ref_count": len(out.references or []),
        "cited_count": len(cited),
        "children": _children(out),
        "reports": reports,
        "angles": angles,
        "angle_status": angle_status,
        "resolved": resolved,
        "left_out": left_out,
        "gaps": gaps,
        "ref_reports": {
            str(n): [int(x) for x in nums] for n, nums in sorted(out.ref_reports.items())
        },
        "stats": _json_safe(dict(out.stats or {})),
    }


def _flat_ura_results(items: list[Any]) -> list[Any]:
    """The URA result rows ``persist_item_references`` reads ``service_ref`` from.

    ``MergedPublishInput.ura_results`` is ``list[Any]``: accept the result rows
    themselves or whole ``UnifiedRetrievalArtifact`` objects (one per report),
    flattened the way agent_search flattens its single URA.
    """
    flat: list[Any] = []
    for item in items or []:
        if hasattr(item, "high_results") or hasattr(item, "medium_results"):
            flat.extend(getattr(item, "high_results", None) or [])
            flat.extend(getattr(item, "medium_results", None) or [])
        elif item is not None:
            flat.append(item)
    return flat


async def publish_merged_result(
    input: MergedPublishInput,
    deps: SearchPublishDeps,
) -> SearchPublishOutput:
    """Persist the merged answer and return the SSE events to forward.

    The workspace_item insert is NOT wrapped in try/except (as agent_search):
    a failure there is a real error the caller must see. The reference write IS
    best-effort — a refs hiccup must not lose the user-visible item.
    """
    logger = deps.logger or logging.getLogger(__name__)
    output = input.output

    title = _build_title(input)
    content_md = _build_content_md(output)
    cited = _cited_numbers(output)
    metadata = _build_metadata(input, cited)
    describe_query = (
        _decode(input.describe_query) if input.describe_query is not None else None
    )

    # PII note: no user identity on this span (recoverable via Supabase join).
    with track_stage(
        "publish.workspace_item",
        conversation_id=input.conversation_id,
        case_id=input.case_id,
        agent_family="publish",
        subtype=SUBTYPE,
        kind=_KIND,
        message_id=input.message_id,
        title_chars=len(title or ""),
        content_md_chars=len(content_md or ""),
        describe_query_chars=len(describe_query or ""),
        confidence=output.confidence,
        report_count=len(output.reports),
        children_count=len(metadata["children"]),
        angle_count=len(output.angles),
        ref_count=metadata["ref_count"],
        cited_count=metadata["cited_count"],
    ) as _pub_span:
        row = create_workspace_item(
            deps.supabase,
            input.user_id,
            kind=_KIND,
            created_by=_CREATED_BY,
            title=title,
            conversation_id=input.conversation_id,
            case_id=input.case_id,
            message_id=input.message_id,
            agent_family=_AGENT_FAMILY,
            content_md=content_md,
            metadata=metadata,
            describe_query=describe_query,
        )

        item_id = row.get("item_id") or row.get("artifact_id") or ""
        if not item_id:
            _pub_span.set(outcome="no_item_id")
            raise RuntimeError("extended_merger: publish returned no item_id")

        _pub_span.set(item_id=item_id, outcome="ok")

        # Sub-query indices are per search, so the merged item stores none.
        try:
            persist_item_references(
                deps.supabase,
                wi_id=item_id,
                references=list(output.references or []),
                ura_results=_flat_ura_results(input.ura_results) or None,
                cited_numbers=cited,
                ref_to_sub_queries={},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "workspace_item_references persist failed (merged item): %s",
                exc, exc_info=True,
            )

        sse_events: list[dict] = [
            {
                "type": "workspace_item_created",
                "item_id": item_id,
                "kind": _KIND,
                "title": row.get("title", title),
                "subtype": SUBTYPE,
                "created_by": _CREATED_BY,
                # The turn's primary item: message_service puts it FIRST in
                # messages.artifact_ids (the chat message's citation target); the
                # event itself comes last so the clients auto-open the merged answer.
                "primary": True,
            },
        ]
        return SearchPublishOutput(item_id=item_id, sse_events=sse_events)


__all__ = ["publish_merged_result"]
