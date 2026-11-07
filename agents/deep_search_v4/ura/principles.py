"""Official judicial principles («مبدأ قضائي») — row → URA shell, fetch, hydrate.

ONE builder for the principle shell, shared by every producer:

    reg_adapter   — a principle the reg reranker kept (``source_row`` carries
                    every column below);
    case_adapter  — a principle the case reranker kept (rule text only);
    enrich_ura    — re-reads every principle from ``official_principles`` and
                    re-fills both kinds of shell from the row, so a principle
                    found by both executors reaches the aggregator identical;
    backend       — ``references_service`` rebuilds persisted ``principle:``
                    refs through the same three functions (plan §3.6).

Identity (plan D1): ``ref_id = f"principle:{official_principles.id}"``, domain
``principles``, source_type ``official_principle``.

Data rules (plan D5, verified live 2026-11-07 on entity 18329):
- ``decisions`` come from ``citations[]`` (``body`` / ``decision_no`` /
  ``date_hijri``) — Latin digits on every one of the 1,794 citations; the body
  by its FULL name. ``official_principles.content`` is never read: it carries
  Arabic-Indic digits and the «م ق د» codes. Digits are still normalised to
  Latin here, defensively — everything below is user-facing.
- A citation with no ``body`` falls back to ``issuing_body_names`` (matched to
  its ``body_code`` through ``issuing_bodies`` when it can be, else the only
  name when there is one).
- Nothing assumes a chapter, a number or a citation exists: the 17486 corpus
  (المحكمة الإدارية العليا) is extracted but unclassified.

All three public functions are fail-soft: a fetch failure logs and leaves the
shells exactly as they were.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from agents.deep_search_v4.ura.schema import PrincipleURAResult

logger = logging.getLogger(__name__)

#: ``official_principles`` columns every reader needs — the same list the reg
#: executor's ``PRINCIPLE_SELECT`` fetches, so a reg-side row and a hydrated
#: row are the same dict.
PRINCIPLE_COLUMNS = (
    "id, principle_ref, entity_ref, principle_no, chapter_no, chapter_title, "
    "text, citations, issuing_bodies, issuing_body_names, source"
)

PRINCIPLE_REF_PREFIX = "principle:"
PRINCIPLE_SOURCE_TYPE = "official_principle"

#: Label when a principle has no number (plan D9).
PRINCIPLE_LABEL_AR = "مبدأ قضائي"

# PostgREST ``in_`` batch size (mirrors ``ura/enrich._ID_BATCH``).
_ID_BATCH = 150

_TO_LATIN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)


def _latin(value: Any) -> str:
    """``value`` as a trimmed string with every Arabic-Indic digit made Latin."""
    return str(value or "").strip().translate(_TO_LATIN_DIGITS)


def _as_int(value: Any) -> int | None:
    try:
        return int(_latin(value)) if _latin(value) else None
    except ValueError:
        return None


def principle_id_from_ref(ref_id: str) -> str:
    """``principle:<uuid>`` → ``<uuid>``; ``""`` for any other prefix."""
    ref = (ref_id or "").strip()
    if not ref.startswith(PRINCIPLE_REF_PREFIX):
        return ""
    return ref[len(PRINCIPLE_REF_PREFIX):].strip()


# ---------------------------------------------------------------------------
# Pure text helpers — shared by the URA projections, the aggregator renderer
# and the source view, so the card, the prompt and the popup say one thing.
# ---------------------------------------------------------------------------


def principle_decisions(row: dict) -> list[dict]:
    """``citations[]`` → ``[{"body", "decision_no", "date_hijri"}]`` (Latin digits).

    A citation with neither a decision number nor a date carries nothing a
    reader can look up and is skipped; a missing body falls back to
    ``issuing_body_names`` (see the module docstring).
    """
    codes = [str(c or "").strip() for c in (row.get("issuing_bodies") or [])]
    names = [str(n or "").strip() for n in (row.get("issuing_body_names") or [])]
    name_by_code = {c: n for c, n in zip(codes, names) if c and n}
    distinct = list(dict.fromkeys(n for n in names if n))
    only_name = distinct[0] if len(distinct) == 1 else ""

    out: list[dict] = []
    for cit in row.get("citations") or []:
        if not isinstance(cit, dict):
            continue
        decision_no = _latin(cit.get("decision_no"))
        date_hijri = _latin(cit.get("date_hijri"))
        if not decision_no and not date_hijri:
            continue
        body = str(cit.get("body") or "").strip()
        if not body:
            body = name_by_code.get(str(cit.get("body_code") or "").strip(), "") or only_name
        out.append(
            {"body": body, "decision_no": decision_no, "date_hijri": date_hijri}
        )
    return out


def principle_body_names(
    decisions: Iterable[dict], issuing_body_names: Iterable[str] = ()
) -> list[str]:
    """Distinct issuing-body names: from the decisions, else ``issuing_body_names``."""
    names = [str((d or {}).get("body") or "").strip() for d in decisions or []]
    names = [n for n in names if n]
    if not names:
        names = [str(n or "").strip() for n in issuing_body_names or []]
    return list(dict.fromkeys(n for n in names if n))


def principle_title(principle_no: int | None, chapter_title: str = "") -> str:
    """«المبدأ رقم N — {الباب}», degrading part by part.

    No number → «مبدأ قضائي» (still followed by the chapter when there is one);
    no chapter → «المبدأ رقم N». Latin digits.
    """
    chapter = (chapter_title or "").strip()
    head = f"المبدأ رقم {principle_no}" if principle_no is not None else PRINCIPLE_LABEL_AR
    return f"{head} — {chapter}" if chapter else head


def _decision_phrase(d: dict) -> str:
    """«162/5 بتاريخ 24/3/1415هـ» — the part shared by the card and the prompt."""
    no = _latin(d.get("decision_no"))
    date = _latin(d.get("date_hijri"))
    if no and date:
        return f"{no} بتاريخ {date}هـ"
    if no:
        return no
    if date:
        return f"بتاريخ {date}هـ"
    return ""


def principle_decision_line(decisions: Iterable[dict]) -> str:
    """The card's decision line: «القرار 162/5 بتاريخ 24/3/1415هـ» (« · » joined).

    A decision with no number reads «قرار بتاريخ …هـ». ``""`` with no decision.
    """
    parts: list[str] = []
    for d in decisions or []:
        phrase = _decision_phrase(d or {})
        if not phrase:
            continue
        has_no = bool(_latin((d or {}).get("decision_no")))
        parts.append(f"القرار {phrase}" if has_no else f"قرار {phrase}")
    return " · ".join(dict.fromkeys(parts))


def principle_decision_lines_for_prompt(decisions: Iterable[dict]) -> list[str]:
    """One «القرار: {decision_no} بتاريخ {date_hijri}هـ — {body}» line per decision."""
    lines: list[str] = []
    for d in decisions or []:
        phrase = _decision_phrase(d or {})
        if not phrase:
            continue
        body = str((d or {}).get("body") or "").strip()
        lines.append(f"القرار: {phrase} — {body}" if body else f"القرار: {phrase}")
    return lines


def _source_fields(row: dict) -> tuple[str, list[int]]:
    """``(source_url, pages)`` from the row's ``source`` jsonb (pages deduped)."""
    source = row.get("source")
    if not isinstance(source, dict):
        return "", []
    url = str(source.get("source_url") or "").strip()
    pages: list[int] = []
    for p in source.get("pages") or []:
        n = _as_int(p)
        if n is not None and n not in pages:
            pages.append(n)
    return url, pages


# ---------------------------------------------------------------------------
# Row → shell
# ---------------------------------------------------------------------------


def principle_from_row(
    row: dict,
    *,
    ref_id: str | None = None,
    relevance: str = "medium",
    reasoning: str = "",
    rrf: float = 0.0,
    source_type: str = PRINCIPLE_SOURCE_TYPE,
) -> PrincipleURAResult:
    """Build a :class:`PrincipleURAResult` from an ``official_principles`` row.

    ``row`` may be a full row (reg search / hydrate) or a partial one — the case
    adapter passes ``{"id", "text"}`` only. Missing columns leave their field at
    its default; ``enrich_ura`` fills them. ``ref_id`` defaults to
    ``principle:<row["id"]>``.
    """
    row = row or {}
    principle_id = str(row.get("id") or "").strip() or principle_id_from_ref(ref_id or "")
    source_url, pages = _source_fields(row)
    return PrincipleURAResult(
        ref_id=ref_id or f"{PRINCIPLE_REF_PREFIX}{principle_id}",
        source_type=source_type or PRINCIPLE_SOURCE_TYPE,
        relevance=relevance if relevance in ("high", "medium") else "medium",
        reasoning=reasoning or "",
        appears_in_sub_queries=[],
        rrf_max=float(rrf or 0.0),
        principle_id=principle_id,
        principle_ref=str(row.get("principle_ref") or "").strip(),
        entity_ref=str(row.get("entity_ref") or "").strip(),
        principle_no=_as_int(row.get("principle_no")),
        chapter_title=str(row.get("chapter_title") or "").strip(),
        text=str(row.get("text") or "").strip(),
        decisions=principle_decisions(row),
        issuing_body_names=[
            str(n).strip() for n in (row.get("issuing_body_names") or []) if str(n or "").strip()
        ],
        source_url=source_url,
        pages=pages,
    )


# ---------------------------------------------------------------------------
# Fetch + hydrate
# ---------------------------------------------------------------------------


def fetch_principles_by_id(supabase, ids: Iterable[str]) -> dict[str, dict]:
    """``official_principles`` rows keyed by id — SYNC, batched ``in_``.

    ``supabase`` must be the service-role client (the table is RLS-on,
    service-role only; the anon key silently returns nothing). Each batch is
    fail-soft: a failure logs and the batch's ids are simply absent.
    """
    unique = sorted({str(i).strip() for i in ids or [] if str(i or "").strip()})
    out: dict[str, dict] = {}
    for start in range(0, len(unique), _ID_BATCH):
        batch = unique[start:start + _ID_BATCH]
        try:
            resp = (
                supabase.table("official_principles")
                .select(PRINCIPLE_COLUMNS)
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                if r.get("id"):
                    out[str(r["id"])] = r
        except Exception as e:  # best-effort — never crash the pipeline
            logger.warning("principles: fetch_principles_by_id batch failed: %s", e)
    return out


#: Fields ``hydrate_principles`` overwrites from the row. Plumbing
#: (``ref_id`` / relevance / reasoning / sub-queries / rrf) is never touched.
_HYDRATED_FIELDS = (
    "principle_id",
    "principle_ref",
    "entity_ref",
    "principle_no",
    "chapter_title",
    "text",
    "decisions",
    "issuing_body_names",
    "source_url",
    "pages",
)


async def hydrate_principles(supabase, results: Iterable[Any]) -> None:
    """Fill principle shells in place from ``official_principles`` (ONE batched read).

    ``async`` — the fetch runs under ``asyncio.to_thread`` (sync client, async
    caller), like every ``ura/enrich`` stage. Await it.

    Non-principle results in ``results`` are ignored. A shell whose row did not
    come back keeps whatever it already carried (a reg-side shell is already
    complete; a case-side shell keeps its rule text), so a DB hiccup degrades to
    the adapter's view, never to an empty card. A non-empty field is never
    overwritten with an empty one.
    """
    shells = [r for r in results or [] if isinstance(r, PrincipleURAResult)]
    if not shells:
        return
    ids = [s.principle_id or principle_id_from_ref(s.ref_id) for s in shells]
    rows = await asyncio.to_thread(fetch_principles_by_id, supabase, ids)
    if not rows:
        return
    for shell, pid in zip(shells, ids):
        row = rows.get(pid)
        if not row:
            continue
        fresh = principle_from_row(row, ref_id=shell.ref_id)
        for name in _HYDRATED_FIELDS:
            value = getattr(fresh, name)
            if value or value == 0:
                setattr(shell, name, value)


__all__ = [
    "PRINCIPLE_COLUMNS",
    "PRINCIPLE_REF_PREFIX",
    "PRINCIPLE_SOURCE_TYPE",
    "PRINCIPLE_LABEL_AR",
    "principle_id_from_ref",
    "principle_decisions",
    "principle_body_names",
    "principle_title",
    "principle_decision_line",
    "principle_decision_lines_for_prompt",
    "principle_from_row",
    "fetch_principles_by_id",
    "hydrate_principles",
]
