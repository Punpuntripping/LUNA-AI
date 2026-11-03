"""Load and render everything the analyzer reads.

Reports come from ``workspace_items``. Sources come from
``retrieval_artifacts.ura_json``, not ``workspace_item_references``: the
aggregator strips uncited sources before publishing, so that table holds only
what a report cited, while the search result still holds everything the reranker
kept. Sources the reranker dropped (``ura_json.dropped``) are deliberately not
read.

Service-role client: the ``user_id`` filters here are load-bearing.
"""
from __future__ import annotations

import html
import logging
from typing import Any, Iterable, Mapping, Sequence

from backend.app.services.masking_service import encode_active
from shared.library.supersession import (
    aggregator_status,
    load_supersessions,
    responder_line,
    riyadh_today,
)

from .models import (
    CONVERSATION_ROWS,
    MAX_REPORTS_WITHOUT_PICK,
    AnalyzerCall,
    ReportRow,
    SourceRow,
    VerifiedQuote,
)
from .tools import DOMAIN_TABLES

logger = logging.getLogger(__name__)

_NO_SUMMARY = "(لا يوجد ملخص)"
_NO_TITLE = "بدون عنوان"
_CIRCULAR_HEAD_CHARS = 200
_SERVICE_HEAD_CHARS = 600


def _esc(value: object) -> str:
    """Stop a message or a stored text forging a structural tag."""
    return html.escape(str(value or ""), quote=False)


def select_reports(
    rows_newest_first: Sequence[Mapping[str, Any]],
    picked_item_ids: Iterable[str],
) -> list[Mapping[str, Any]]:
    """All reports up to the limit; above it the picks, else the newest."""
    if len(rows_newest_first) <= MAX_REPORTS_WITHOUT_PICK:
        return list(rows_newest_first)
    wanted = {str(i) for i in picked_item_ids}
    chosen = [r for r in rows_newest_first if str(r.get("item_id")) in wanted]
    return chosen or list(rows_newest_first[:MAX_REPORTS_WITHOUT_PICK])


def load_reports(supabase: Any, call: AnalyzerCall) -> list[ReportRow]:
    query = (
        supabase.table("workspace_items")
        .select("item_id, wi_seq, title, content_md, summary, describe_query, metadata")
        .eq("user_id", call.user_id)
        .eq("conversation_id", call.conversation_id)
        .eq("kind", "agent_search")
        .is_("deleted_at", "null")
    )
    if call.reports_before:
        query = query.lt("created_at", call.reports_before)
    rows = query.order("created_at", desc=True).execute().data or []

    reports: list[ReportRow] = []
    for i, row in enumerate(select_reports(rows, call.picked_item_ids), start=1):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        confidence = metadata.get("confidence")
        gaps = metadata.get("gaps")
        reports.append(ReportRow(
            label=f"R{i}",
            item_id=str(row["item_id"]),
            wi_seq=row.get("wi_seq"),
            # Stored real; every field below reaches the LLM.
            title=encode_active(row.get("title") or ""),
            content_md=encode_active(row.get("content_md") or ""),
            describe_query=encode_active(row.get("describe_query") or ""),
            confidence=confidence if confidence in ("high", "medium", "low") else "medium",
            gaps=[encode_active(str(g)) for g in gaps] if isinstance(gaps, list) else [],
            summary=encode_active(row.get("summary") or ""),
        ))
    return reports


def _ref_tail(ref_id: str) -> str:
    return ref_id.partition(":")[2] or ref_id


def _open_key(domain: str, ura_row: Mapping[str, Any], ref: Mapping[str, Any], ref_id: str) -> str:
    if domain == "compliance":
        # ref_id holds a hash here; the row carries the real key.
        return str(ura_row.get("service_ref") or ref.get("item_id") or "")
    return _ref_tail(ref_id)


def _stored_text(domain: str, row: Mapping[str, Any]) -> str:
    keys = {
        "regulations": ("chunk_content", "chunk_agent_content"),
        "cases": ("case_content",),
        "circulars": ("content",),
        "compliance": ("service_context",),
    }.get(domain, ())
    for key in keys:
        if row.get(key):
            return str(row[key])
    return ""


def _source_name(domain: str, row: Mapping[str, Any], chunk: Mapping[str, Any]) -> str:
    if domain == "regulations":
        law = row.get("reg_title") or ""
        title = chunk.get("title") or ""
        return " — ".join(p for p in (law, title) if p) or _NO_TITLE
    if domain == "cases":
        return row.get("title") or f"حكم {row.get('court') or ''}".strip()
    if domain == "circulars":
        return row.get("title") or "تعميم"
    return row.get("service_name") or row.get("intro_title") or "خدمة حكومية"


def _cited_suffix(cited_in: Sequence[tuple[str, int]]) -> str:
    if not cited_in:
        return ""
    return "  (" + "، ".join(f"{label}[{n}]" for label, n in cited_in) + ")"


def _render_block(source: SourceRow, row: Mapping[str, Any], chunk: Mapping[str, Any]) -> str:
    """The reranker's labels, in its order, with no ranking scores."""
    head = _cited_suffix(source.cited_in)
    domain = source.domain
    if domain == "regulations":
        lines = [
            f"### [{source.label}] {_esc(chunk.get('title') or _NO_TITLE)}{head}",
            f"**النظام**: {_esc(row.get('reg_title'))}",
        ]
        if source.status:
            lines.append(f"**حالة النظام**: {_esc(source.status)}")
        lines.append(f"**نطاق النظام**: {_esc(row.get('reg_scope'))}")
        lines.append(f"**ملخص المقطع**: {_esc(chunk.get('summary') or _NO_SUMMARY)}")
        return "\n".join(lines)
    if domain == "cases":
        place = " · ".join(
            str(row[k]) for k in ("court", "city", "date_hijri") if row.get(k)
        )
        return "\n".join([
            f"### [{source.label}] حكم: {_esc(source.name)}{head}",
            f"**المحكمة**: {_esc(place)}",
            f"**ملخص الحكم**: {_esc(row.get('short_summary') or _NO_SUMMARY)}",
        ])
    if domain == "circulars":
        return "\n".join([
            f"### [{source.label}] تعميم: {_esc(source.name)}{head}",
            f"**الجهة**: {_esc(row.get('entity_name'))}",
            _esc(str(row.get("content") or "")[:_CIRCULAR_HEAD_CHARS]),
        ])
    return "\n".join([
        f"### [{source.label}] خدمة: {_esc(source.name)}{head}",
        f"**الجهة**: {_esc(row.get('provider_name'))}",
        _esc(str(row.get("service_context") or "")[:_SERVICE_HEAD_CHARS]),
    ])


def _load_chunks(supabase: Any, chunk_ids: list[str]) -> dict[str, dict]:
    """Chunk title and summary. The search result does not carry the summary."""
    if not chunk_ids:
        return {}
    try:
        rows = (
            supabase.table("chunks_v2")
            .select("id, title, summary, regulation_id")
            .in_("id", chunk_ids)
            .execute()
            .data
        ) or []
    except Exception as exc:  # noqa: BLE001 — a source without a summary still counts
        logger.warning("artifact_analyzer: chunk summaries failed: %s", exc)
        return {}
    return {str(r["id"]): r for r in rows}


def load_sources(supabase: Any, reports: Sequence[ReportRow], user_id: str) -> list[SourceRow]:
    """One deduplicated list: ``[n]`` restarts per report, so the same circular
    can be R1[34] and R2[12]. Code keeps the label map; the model never sees an id.
    """
    if not reports:
        return []
    labels = {r.item_id: r.label for r in reports}
    ids = list(labels)

    artifacts = (
        supabase.table("retrieval_artifacts")
        .select("artifact_id, ura_json")
        .in_("artifact_id", ids)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .execute()
        .data
    ) or []
    refs = (
        supabase.table("workspace_item_references")
        .select("wi_id, n, ref_id, domain, item_id, used")
        .in_("wi_id", ids)
        .execute()
        .data
    ) or []

    cited: dict[str, list[tuple[str, int]]] = {}
    ref_rows: dict[str, dict] = {}
    for ref in refs:
        ref_id = str(ref.get("ref_id") or "")
        if not ref_id:
            continue
        ref_rows.setdefault(ref_id, ref)
        if ref.get("used") and str(ref.get("wi_id")) in labels:
            cited.setdefault(ref_id, []).append((labels[str(ref["wi_id"])], int(ref["n"])))

    # Newest report first, then high before medium, so labels read in that order.
    by_artifact = {str(a["artifact_id"]): a.get("ura_json") or {} for a in artifacts}
    ordered: dict[str, dict] = {}
    found_in: dict[str, list[str]] = {}
    for report in reports:
        ura = by_artifact.get(report.item_id) or {}
        for row in (ura.get("high_results") or []) + (ura.get("medium_results") or []):
            ref_id = str(row.get("ref_id") or "")
            if ref_id:
                ordered.setdefault(ref_id, row)
                ids = found_in.setdefault(ref_id, [])
                if report.item_id not in ids:
                    ids.append(report.item_id)
    # The artifact write is best-effort: a cited source with no search row still counts.
    by_label = {r.label: r.item_id for r in reports}
    for ref_id, ref in ref_rows.items():
        if ref_id in cited and ref_id not in ordered:
            ordered[ref_id] = {"ref_id": ref_id, "domain": ref.get("domain")}
            found_in[ref_id] = [by_label[label] for label, _ in cited[ref_id]]

    # A domain the tool cannot open could never be quoted, so it is not offered.
    ordered = {k: v for k, v in ordered.items() if v.get("domain") in DOMAIN_TABLES}

    chunk_ids = [
        _ref_tail(ref_id) for ref_id, row in ordered.items()
        if row.get("domain") == "regulations"
    ]
    chunks = _load_chunks(supabase, chunk_ids)

    sources: list[SourceRow] = []
    for i, (ref_id, row) in enumerate(ordered.items(), start=1):
        domain = str(row.get("domain") or "")
        chunk = chunks.get(_ref_tail(ref_id), {}) if domain == "regulations" else {}
        source = SourceRow(
            label=f"S{i}",
            ref_id=ref_id,
            domain=domain,
            open_key=_open_key(domain, row, ref_rows.get(ref_id, {}), ref_id),
            name=_source_name(domain, row, chunk),
            block="",
            status=str(row.get("reg_status") or ""),
            stored_text=_stored_text(domain, row),
            regulation_id=str(chunk.get("regulation_id") or ""),
            cited_in=cited.get(ref_id, []),
            report_ids=found_in.get(ref_id, []),
        )
        source.block = _render_block(source, row, chunk)
        sources.append(source)
    return sources


def law_change_line(supabase: Any, quotes: Sequence[VerifiedQuote]) -> str:
    """The notice for a quoted law in transition, as a search reply carries it.

    Computed live: a transition is left out of the stored search result, and
    ``reg_status`` is empty while one runs, so nothing stored says so.
    """
    reg_ids = list(dict.fromkeys(r for q in quotes for r in q.regulation_ids))
    if not reg_ids:
        return ""
    try:
        supersessions = load_supersessions(supabase)
        today = riyadh_today()
        return responder_line(
            transition
            for reg_id in reg_ids
            for transition in aggregator_status(supersessions, {"id": reg_id}, today).transitions
        )
    except Exception as exc:  # noqa: BLE001 — a missing notice must not cost the answer
        logger.warning("artifact_analyzer: law change notice failed: %s", exc)
        return ""


def _render_report(report: ReportRow) -> str:
    # Reports published before gaps were saved carry them in their summary.
    if report.gaps:
        gaps = "\n".join(f"  <gap>{_esc(g)}</gap>" for g in report.gaps)
    elif report.summary:
        gaps = f"  <report_summary>{_esc(report.summary)}</report_summary>"
    else:
        gaps = ""
    return (
        f'<report label="{report.label}" confidence="{report.confidence}">\n'
        f"<title>{_esc(report.title)}</title>\n"
        f"<asked>{_esc(report.describe_query)}</asked>\n"
        f"<content_md>\n{_esc(report.content_md)}\n</content_md>\n"
        f"<gaps>\n{gaps}\n</gaps>\n"
        f"</report>"
    )


def _conversation_rows(messages: Sequence[Any]) -> list[Any]:
    """The rows before the current message, which is rendered on its own."""
    rows = list(messages)
    if rows and getattr(rows[-1], "role", "") == "user":
        rows = rows[:-1]
    return rows[-CONVERSATION_ROWS:]


def render_turn_message(
    call: AnalyzerCall,
    reports: Sequence[ReportRow],
    sources: Sequence[SourceRow],
) -> str:
    conversation = "\n".join(
        f'<message role="{getattr(m, "role", "")}">{_esc(getattr(m, "content", ""))}</message>'
        for m in _conversation_rows(call.recent_messages)
    )
    return "\n\n".join([
        f"<current_message>{_esc(encode_active(call.question))}</current_message>",
        f"<conversation>\n{conversation}\n</conversation>",
        "\n\n".join(_render_report(r) for r in reports),
        "<sources>\n" + "\n\n".join(s.block for s in sources) + "\n</sources>",
    ])
