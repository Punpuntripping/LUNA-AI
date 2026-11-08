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

from agents.deep_search_v4.ura.principles import (
    principle_body_names,
    principle_decision_line,
    principle_title,
)
from agents.deep_search_v4.ura.services_unfold import build_service_aggregator_content
from agents.utils.reading_corrections import entries_of, render_block
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
    SearchScope,
    SourceRow,
    VerifiedQuote,
)
from .tools import DOMAIN_TABLES

logger = logging.getLogger(__name__)

#: ``produced_by`` key → what that search covers. Services, circulars and
#: official principles all come through the regulation search's topic lookup;
#: ``compliance_search`` is the retired loop and never runs, so it is not listed.
_SEARCH_GROUPS = (
    ("reg_search", "regulations", "الأنظمة واللوائح والتعاميم والخدمات الحكومية"),
    ("case_search", "cases", "الأحكام القضائية"),
)
_QUERY_DOMAIN = {"regulations": "الأنظمة", "cases": "الأحكام"}
_NO_SEARCH_RECORD = "لا يوجد سجل لهذا البحث"

_NO_SUMMARY = "(لا يوجد ملخص)"
#: On a service the model can read in full with ``read_full_text``.
_HAS_GUIDE = "**له دليل شامل**"
_NO_TITLE = "بدون عنوان"
_CIRCULAR_HEAD_CHARS = 200
_SERVICE_HEAD_CHARS = 600
# A principle's rule is usually one or two sentences, so the head is most often
# the whole rule. It is still only the summary the model chooses from: a quote
# counts only after ``open_sources`` reads it (verify._verify_quote).
_PRINCIPLE_HEAD_CHARS = 600


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
    chosen = select_reports(rows, call.picked_item_ids)
    uras = _load_uras(supabase, [str(r["item_id"]) for r in chosen], call.user_id)

    reports: list[ReportRow] = []
    for i, row in enumerate(chosen, start=1):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        confidence = metadata.get("confidence")
        gaps = metadata.get("gaps")
        ura = uras.get(str(row["item_id"])) or {}
        saved = entries_of(metadata)
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
            searched=search_scope(ura),
            ura=ura,
            corrections=encode_active(render_block(saved)),
            corrected_texts=[encode_active(str(e["report_text"])) for e in saved],
        ))
    return reports


def _load_uras(supabase: Any, item_ids: list[str], user_id: str) -> dict[str, dict]:
    """Each report's stored search result. The write is best-effort, so one may be missing."""
    if not item_ids:
        return {}
    rows = (
        supabase.table("retrieval_artifacts")
        .select("artifact_id, ura_json")
        .in_("artifact_id", item_ids)
        .eq("user_id", user_id)
        .is_("deleted_at", "null")
        .execute()
        .data
    ) or []
    return {
        str(r["artifact_id"]): r["ura_json"]
        for r in rows if isinstance(r.get("ura_json"), dict)
    }


def search_scope(ura: Mapping[str, Any]) -> SearchScope | None:
    """What the search looked for, or None when it left no record of its queries."""
    sub_queries = [q for q in ura.get("sub_queries") or [] if isinstance(q, Mapping)]
    queries = [
        (_QUERY_DOMAIN.get(str(q.get("domain") or ""), ""), encode_active(str(q.get("query") or "").strip()))
        for q in sub_queries
        if str(q.get("query") or "").strip()
    ]
    if not queries:
        return None
    produced = ura.get("produced_by") if isinstance(ura.get("produced_by"), Mapping) else None
    if produced is None:
        # Older results: the queries' own domains say which searches ran.
        domains = {str(q.get("domain") or "") for q in sub_queries}
        ran = {key: domain in domains for key, domain, _ in _SEARCH_GROUPS}
    else:
        ran = {key: bool(produced.get(key)) for key, _, _ in _SEARCH_GROUPS}
    return SearchScope(
        queries=queries,
        searched_in=[label for key, _, label in _SEARCH_GROUPS if ran[key]],
        not_searched_in=[label for key, _, label in _SEARCH_GROUPS if not ran[key]],
        sectors=[str(s) for s in ura.get("sector_filter") or [] if str(s).strip()],
    )


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
        "principles": ("text",),
    }.get(domain, ())
    for key in keys:
        if row.get(key):
            return str(row[key])
    return ""


def _search_view(domain: str, row: Mapping[str, Any]) -> str:
    """The text the aggregator read for a ruling or a service (read_full_text).

    A ruling's is ``case_content``: ``cases.summary`` clipped, as stored. A
    service's is rebuilt from the stored row with the aggregator's own builder.
    """
    if domain == "cases":
        return str(row.get("case_content") or "")
    if domain == "compliance":
        return build_service_aggregator_content(
            service_name=str(row.get("service_name") or ""),
            intro_title=str(row.get("intro_title") or ""),
            provider_name=str(row.get("provider_name") or ""),
            intro_description=str(row.get("intro_description") or ""),
            steps=row.get("steps") or [],
            requirements=row.get("requirements") or [],
            required_documents=row.get("required_documents") or [],
            service_url=str(row.get("service_url") or ""),
            url=str(row.get("url") or ""),
            fallback_context=str(row.get("service_context") or ""),
        )
    return ""


def _guided_services(supabase: Any, service_refs: list[str]) -> dict[str, str]:
    """service_ref → services.id, for the services that have «الدليل الشامل»."""
    if not service_refs:
        return {}
    try:
        services = (
            supabase.table("services")
            .select("id, service_ref")
            .in_("service_ref", service_refs)
            .execute()
            .data
        ) or []
        ref_by_id = {str(r["id"]): str(r["service_ref"]) for r in services}
        if not ref_by_id:
            return {}
        guides = (
            supabase.table("service_guides")
            .select("service_id")
            .in_("service_id", list(ref_by_id))
            .eq("is_canonical", True)
            .execute()
            .data
        ) or []
    except Exception as exc:  # noqa: BLE001 — without it the service is read as before
        logger.warning("artifact_analyzer: guide lookup failed: %s", exc)
        return {}
    return {
        ref_by_id[str(g["service_id"])]: str(g["service_id"])
        for g in guides if str(g.get("service_id")) in ref_by_id
    }


def _source_name(domain: str, row: Mapping[str, Any], chunk: Mapping[str, Any]) -> str:
    if domain == "regulations":
        law = row.get("reg_title") or ""
        title = chunk.get("title") or ""
        return " — ".join(p for p in (law, title) if p) or _NO_TITLE
    if domain == "cases":
        return row.get("title") or f"حكم {row.get('court') or ''}".strip()
    if domain == "circulars":
        return row.get("title") or "تعميم"
    if domain == "principles":
        return _principle_name(row)
    return row.get("service_name") or row.get("intro_title") or "خدمة حكومية"


def _principle_number(row: Mapping[str, Any]) -> int | None:
    try:
        value = row.get("principle_no")
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _principle_bodies(row: Mapping[str, Any]) -> str:
    """The issuing bodies by full name — from the decisions, else the row's list."""
    return "، ".join(
        principle_body_names(row.get("decisions") or [], row.get("issuing_body_names") or [])
    )


def _principle_name(row: Mapping[str, Any]) -> str:
    """«المبدأ رقم N — {الباب} — {الجهة}»: the card's copy label.

    ``Reference.label`` for an ``official_principle`` is title + bodies, because
    the body alone does not tell two principles apart and the title alone does
    not say who issued it — a reply that names this source needs both. A cited
    principle with no search row (the artifact write is best-effort) still
    reads «مبدأ قضائي».
    """
    title = principle_title(_principle_number(row), str(row.get("chapter_title") or ""))
    return " — ".join(p for p in (title, _principle_bodies(row)) if p)


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
    if domain == "principles":
        # Its own kind, never the service fall-through below: the reranker
        # names it «مبدأ قضائي رسمي» and so does this. Bodies and decisions come
        # from the stored ``decisions`` (citations, Latin digits, full names).
        title = principle_title(_principle_number(row), str(row.get("chapter_title") or ""))
        lines = [f"### [{source.label}] مبدأ قضائي رسمي: {_esc(title)}{head}"]
        bodies = _principle_bodies(row)
        if bodies:
            lines.append(f"**الجهة**: {_esc(bodies)}")
        decisions = principle_decision_line(row.get("decisions") or [])
        if decisions:
            lines.append(f"**القرار**: {_esc(decisions)}")
        lines.append(_esc(str(row.get("text") or "")[:_PRINCIPLE_HEAD_CHARS]))
        return "\n".join(lines)
    lines = [
        f"### [{source.label}] خدمة: {_esc(source.name)}{head}",
        f"**الجهة**: {_esc(row.get('provider_name'))}",
    ]
    if source.has_guide:
        lines.append(_HAS_GUIDE)
    lines.append(_esc(str(row.get("service_context") or "")[:_SERVICE_HEAD_CHARS]))
    return "\n".join(lines)


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


def load_sources(
    supabase: Any,
    reports: Sequence[ReportRow],
    *,
    full_text: bool = False,
) -> list[SourceRow]:
    """One deduplicated list: ``[n]`` restarts per report, so the same circular
    can be R1[34] and R2[12]. Code keeps the label map; the model never sees an id.

    Reads each report's search result from ``ReportRow.ura``, which ``load_reports``
    already fetched under the user filter. With ``full_text`` on, services that
    have «الدليل الشامل» are marked, so the model knows it can read them in full.
    """
    if not reports:
        return []
    labels = {r.item_id: r.label for r in reports}
    ids = list(labels)

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
    ordered: dict[str, dict] = {}
    found_in: dict[str, list[str]] = {}
    for report in reports:
        ura = report.ura
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

    built: list[tuple[SourceRow, Mapping[str, Any], Mapping[str, Any]]] = []
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
            search_view=_search_view(domain, row),
        )
        built.append((source, row, chunk))

    if full_text:
        guided = _guided_services(supabase, [
            s.open_key for s, _, _ in built if s.domain == "compliance" and s.open_key
        ])
        for source, _, _ in built:
            if source.domain == "compliance":
                source.guide_service_id = guided.get(source.open_key, "")

    for source, row, chunk in built:
        source.block = _render_block(source, row, chunk)
    return [source for source, _, _ in built]


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


def _render_searched(scope: SearchScope | None) -> str:
    if scope is None:
        return f"<searched>{_NO_SEARCH_RECORD}</searched>"
    lines = ["<searched>"]
    if scope.searched_in:
        lines.append(f"<searched_in>{_esc('، '.join(scope.searched_in))}</searched_in>")
    if scope.not_searched_in:
        lines.append(f"<not_searched_in>{_esc('، '.join(scope.not_searched_in))}</not_searched_in>")
    if scope.sectors:
        lines.append(f"<sectors>{_esc('، '.join(scope.sectors))}</sectors>")
    for where, text in scope.queries:
        attr = f' in="{where}"' if where else ""
        lines.append(f"<query{attr}>{_esc(text)}</query>")
    lines.append("</searched>")
    return "\n".join(lines)


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
        f"{_render_searched(report.searched)}\n"
        f"<content_md>\n{_esc(report.content_md)}\n</content_md>\n"
        f"<gaps>\n{gaps}\n</gaps>\n"
        f"{_render_corrections(report)}"
        f"</report>"
    )


def _render_corrections(report: ReportRow) -> str:
    """Errors a full text already showed in this report, so none is flagged twice."""
    if not report.corrections:
        return ""
    return f"<corrections>\n{_esc(report.corrections)}\n</corrections>\n"


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
