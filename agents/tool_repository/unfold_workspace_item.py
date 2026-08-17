"""unfold_workspace_item — read a workspace item AND its used citations.

Replaces the old ``read_workspace_item`` tool. Where ``read_workspace_item``
returned only ``content_md``, this returns the body PLUS a used-only,
``n``-keyed manifest of every source cited inside it::

    [1] {doc_type}: {regulation name} — {chunk title}   (regulations domain)
    [2] قضية: [{case number}] {card title} — {snippet}   (cases domain)
    [3] {service name}                                   (compliance domain)
    [4] تعميم: {title} — {entity}                        (circulars domain)

The numbers match the ``[n]`` citation markers in ``content_md`` (the same
``workspace_item_references.n``), so an agent reading the item can map any
``[n]`` in the body to the exact named source. The content-only read could
not do this — which is why the router/planner kept failing to recognise a
user's reference to a *specific named regulation* (e.g. «نظام اشتراطات
المطاعم») that lived inside a prior search result.

THE CARD IS THE CONTRACT (simple_search plan §2.3.1 / §2.3.2)
-------------------------------------------------------------
**A user names a source by what is printed on the card in front of them.** So
every line here is bounded BELOW and ABOVE by the المراجع panel's own rendering
of that same ref (``frontend/components/workspace/ReferencePanel.tsx``, fed by
``backend/app/services/references_service.fetch_item_references_payload``):

* *at least the card* — the panel's **type chip** (``regulations_v2.doc_type_raw``
  — لائحة / تنظيم / دليل / مواصفة قياسية — where the domain label would say the
  blanket «نظام», ``ReferencePanel.tsx:382-385``) and, for a ruling, the panel's
  **derived card title** including its «حكم {court}» fallback. A user saying
  «اللائحة اللي في المراجع» or «حكم المحكمة التجارية» is quoting exactly those
  two strings, and neither existed in this manifest before.
* *and no more than the card* — every body-derived string is truncated to the
  card's own snippet cap (``preprocessor.build_snippet``, 500 chars). The user
  never saw past that cap either, so nothing quotable is lost — and the cases
  line stops shipping the FULL ``cases.summary`` (measured p50 2,375 / p90 3,512
  chars on live cited rows) just to let a model pick one ref out of eight.

**Layering.** ``fetch_item_references_payload`` and ``_build_case_shells`` live
in ``backend/`` and ``agents/`` must never import from ``backend/`` — so the
card's derivations are REIMPLEMENTED here from the same agents-side primitives
the panel path itself calls (``shared.seo.judgment_naming.judgment_subject``,
``aggregator.preprocessor.build_snippet``, ``ura.schema.CaseURAResult``). Each
copy names its backend source in a comment; the parity tests
(``tests/test_unfold_workspace_item.py``) assert both bounds per domain so the
two renderings cannot drift apart again.

Why a tool (not eager context): the manifest costs a couple of batched joins
per WI, so it loads on demand — the agent calls it when the per-item summary
in its context isn't enough.

Resolution layer (:func:`resolve_used_sources`, :func:`render_unfold_md`) is
pure / batched — card fields only, no ``source_view`` and no full bodies — so it
unit-tests without an agent or a live DB. Mirrors how the item_analyzer's
callers unfold ``workspace_item_references`` (used refs → per-domain source
rows), but card-shaped and deterministic (no LLM).

Registration::

    from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
    register_unfold_workspace_item(agent)   # deps must expose .supabase, .user_id, .wi_alias_map

The deps object must structurally satisfy :class:`HasWorkspaceContext`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from pydantic_ai import Agent, RunContext

# Agents-side ONLY (the layering constraint above). These three are the exact
# primitives the backend panel path runs a reference through, which is what makes
# the reimplementation below a copy rather than a fork:
#   * ``build_snippet``      — references_service._load_references:298 calls it
#                              for every non-compliance card's snippet.
#   * ``_build_snippet_text``— the tail of ``build_snippet`` (sentence-boundary
#                              truncation); used here to cap card TITLES, which
#                              have no URA shell to route through.
#   * ``judgment_subject``   — preprocessor._reference_from_ura:521 titles every
#                              cases card with it (and it cut the 10,000
#                              published /judgments slugs — never re-derive it).
from agents.deep_search_v4.aggregator.preprocessor import (
    _build_snippet_text,
    build_snippet,
)
from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections
from agents.deep_search_v4.ura.schema import CaseURAResult
from shared.seo.judgment_naming import judgment_subject

logger = logging.getLogger(__name__)


# --- Schema config: a table/column rename is a one-line change here. ----------
_ITEMS_TABLE = "workspace_items"
_REFS_TABLE = "workspace_item_references"
_CHUNKS_TABLE = "chunks_v2"
_REGS_TABLE = "regulations_v2"
_CASES_TABLE = "cases"
_SERVICES_TABLE = "services"
_CIRCULARS_TABLE = "circulars"

# PostgREST `.in_()` batch size — matches references_service / enrich.py.
_ID_BATCH = 150

# Header for the citation manifest appended after content_md.
_MANIFEST_HEADER = "## المصادر المستخدمة في هذا العنصر"
_FALLBACK_LINE = "(مصدر غير متوفر)"

# --- Card parity (plan §2.3.1 / §2.3.2) ---------------------------------------
# THE cap. 500 == ``preprocessor.build_snippet``'s default (preprocessor.py:364),
# which is the cap the المراجع card's snippet is built with — so matching it
# exactly is what makes "no more than the card" provable rather than asserted.
# A tighter preview cap is the searcher's call: pass ``snippet_max_chars`` down
# from :func:`unfold_item`; do not fork this constant.
_SNIPPET_MAX_CHARS = 500

# The panel's type chip per domain — ``DOMAIN_META`` in ReferencePanel.tsx:119-135.
# Regulations override theirs with ``regulations_v2.doc_type_raw`` when the corpus
# determined one (ReferencePanel.tsx:382-385); the other three labels ARE the real
# thing already. Compliance is listed for completeness only: its line stays
# name-only, mirroring the panel deliberately blanking that card's snippet
# (references_service.py:298) because ``services.service_name_ar`` is
# «{الجهة} - {اسم الخدمة}» on all 4,717 rows — the name IS the whole card.
_DOMAIN_TYPE_LABELS = {
    "regulations": "نظام",
    "cases": "قضية",
    "compliance": "خدمة حكومية",
    "circulars": "تعميم",
}

# ``regulations_v2.doc_type_raw`` not-determined sentinel. Normalised away so a
# card is never labelled «غير محدد» — mirrors ura/enrich._doc_type_label:99-105,
# which is where the panel's own doc_type is cleaned.
_DOC_TYPE_UNSPECIFIED = "غير محدد"

# Collapses the newlines a summary/snippet carries: the manifest is ONE line per
# source ("[n] {text}"), so a raw markdown snippet would break the [n] alignment
# the whole tool exists to provide.
_WS_RE = re.compile(r"\s+")


def _one_line(text: str) -> str:
    """Whitespace-collapse a card string so it survives the one-line manifest.

    Applied AFTER truncation, never before: the card's snippet is cut on its own
    line/sentence boundaries (``_build_snippet_text`` treats ``\\n`` as a
    terminator), so collapsing first would move the cut and the agent would stop
    seeing the same characters the user saw.
    """
    return _WS_RE.sub(" ", (text or "").strip()).strip()


def _cap(text: str, max_chars: int) -> str:
    """Truncate one card-derived string to the card's cap, then flatten it.

    ``_build_snippet_text`` is the same helper ``build_snippet`` ends in, so a
    capped title/name is cut exactly where the panel would cut it.
    """
    return _one_line(_build_snippet_text(text or "", max_chars=max_chars))


def _reg_type_label(doc_type_raw: str | None) -> str:
    """The regulations card's type chip: ``doc_type_raw`` → «نظام» fallback.

    Reimplements ``ReferencePanel.tsx:382-385`` (``reference.doc_type?.trim() ||
    meta.label``) over ``ura/enrich._doc_type_label``'s normalisation. Live
    measurement (2026-08-15): 1,794 of the 3,081 used regulations refs that join
    a regulation row — **58.2%** — carry a chip that is NOT «نظام»: لائحة تنفيذية
    (602), دليل (222), لائحة (209), قواعد (117), مبادئ وأحكام (111), إجراءات
    (108), متطلبات (89), تنظيم (76), … On 58% of reg refs the user is therefore
    reading a word this manifest could not show at all.
    """
    value = (doc_type_raw or "").strip()
    if not value or value == _DOC_TYPE_UNSPECIFIED:
        return _DOMAIN_TYPE_LABELS["regulations"]
    return value


# --- WI alias resolution (migration 052 / agent communication protocol) -------
# The LLM emits ``WI-{seq}`` aliases (e.g. ``"WI-3"``); the tool resolves them
# against the deps' ``wi_alias_map``. A raw UUID is accepted verbatim for
# backward compat. Mirrors agents/router/router.py + planner/agent.py.

_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _resolve_wi_alias(alias: str, alias_map: dict[int, str]) -> str | None:
    """Resolve ``"WI-{seq}"`` → ``workspace_items.item_id`` UUID.

    Returns the UUID on success, ``None`` if the alias is malformed or its
    seq is not in the conversation's map. A raw UUID is accepted verbatim.
    """
    if not alias:
        return None
    s = alias.strip()
    m = _WI_ALIAS_RE.match(s)
    if m:
        try:
            seq = int(m.group(1))
        except ValueError:
            return None
        return alias_map.get(seq)
    if _UUID_RE.match(s):
        return s
    return None


@runtime_checkable
class HasWorkspaceContext(Protocol):
    """Structural deps contract for the tool.

    Any concrete deps object (``RouterDeps`` / ``PlannerDeps`` /
    ``WriterPlannerDeps``) satisfies this — they all carry these three.
    Kept loose (``object``) to avoid hard imports of the supabase client here.
    """

    supabase: object
    user_id: str
    wi_alias_map: dict


# --------------------------------------------------------------------------- #
# Pure render — unit-testable in isolation.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceLine:
    """One cited source, keyed by its ``[n]`` citation number.

    ``text`` is the rendered descriptor WITHOUT the ``[n]`` prefix — the
    renderer adds it. ``domain`` is kept for telemetry / testing.
    """

    n: int
    text: str
    domain: str


def render_unfold_md(content_md: str, lines: Sequence[SourceLine]) -> str:
    """Render the unfolded markdown: ``content_md`` + a used-only manifest.

    Lines are sorted by ``n`` ascending so they line up with the ``[n]``
    markers in ``content_md``. When there are no cited sources the manifest
    is omitted entirely (the tool degrades to a plain content read). When
    ``content_md`` is empty but sources exist, the manifest still renders.
    """
    body = (content_md or "").strip()
    if not lines:
        return body

    rendered = "\n".join(
        f"[{ln.n}] {ln.text}" for ln in sorted(lines, key=lambda x: x.n)
    )
    manifest = f"{_MANIFEST_HEADER}\n{rendered}"
    if not body:
        return manifest
    return f"{body}\n\n---\n{manifest}"


# --------------------------------------------------------------------------- #
# Supabase reads — sync client (matches the rest of agents/). Batched.
# --------------------------------------------------------------------------- #


def _select_in(supabase, table: str, columns: str, col: str, ids: list[str]) -> list[dict]:
    """Batched ``SELECT columns FROM table WHERE col IN (ids)``.

    Returns the merged row list. Never raises — a failed batch logs and
    contributes nothing (the corresponding manifest lines degrade to a stub).
    """
    out: list[dict] = []
    uniq = sorted({i for i in ids if i})
    for i in range(0, len(uniq), _ID_BATCH):
        batch = uniq[i:i + _ID_BATCH]
        try:
            resp = supabase.table(table).select(columns).in_(col, batch).execute()
            out.extend(getattr(resp, "data", None) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("unfold: %s batch select failed: %s", table, exc)
    return out


def _fetch_item(supabase, item_id: str, user_id: str) -> dict | None:
    """Fetch ``{title, content_md}`` for one WI in the user's scope.

    The service-role client bypasses RLS, so ``.eq("user_id", user_id)`` is
    the load-bearing scope filter (mirrors read_workspace_item).
    """
    try:
        resp = (
            supabase.table(_ITEMS_TABLE)
            .select("title, content_md")
            .eq("item_id", item_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("unfold: item fetch failed for %s: %s", item_id, exc)
        return None
    if resp and getattr(resp, "data", None):
        return resp.data
    return None


def _fetch_used_refs(supabase, wi_id: str) -> list[dict]:
    """Fetch the used-only ref rows for a WI, ordered by ``n``.

    Returns rows ``{item_id, ref_id, domain, n}``. ``used=True`` filter is the
    user's spec: unused references never appear in the manifest.
    """
    try:
        resp = (
            supabase.table(_REFS_TABLE)
            .select("item_id, ref_id, domain, n")
            .eq("wi_id", wi_id)
            .eq("used", True)
            .order("n", desc=False)
            .execute()
        )
        return list(getattr(resp, "data", None) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("unfold: used-ref fetch failed for %s: %s", wi_id, exc)
        return []


def _reg_chunk_id(row: dict) -> str:
    """``chunks_v2.id`` for a regulations ref row.

    Prefers the migration-050 ``item_id`` UUID; falls back to stripping the
    ``reg:`` prefix off ``ref_id`` (zero-cost — the reg ref_id IS the chunk
    uuid). Mirrors references_service._reg_chunk_id_from_row.
    """
    item_id = row.get("item_id")
    if item_id:
        return str(item_id)
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith("reg:"):
        return ref_id[4:]
    return ""


def _resolve_regulations(
    supabase, rows: list[dict], *, snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> list[SourceLine]:
    """Build ``[n] {doc_type}: {regulation name} — {chunk title}`` lines.

    Two batched joins: chunks_v2 (id → title, regulation_id) then
    regulations_v2 (id → clean_title|title, doc_type_raw). A row whose chunk
    can't be resolved degrades to a stub line so its ``[n]`` is never silently
    lost.

    ``doc_type`` is the CARD'S OWN TYPE CHIP (plan §2.3.1 divergence 1): the
    panel prints لائحة / تنظيم / دليل / مواصفة قياسية where this manifest used to
    imply the blanket «نظام», so «اللائحة اللي في المراجع» named a string no
    agent could see. Chip first, mirroring the card's own reading order (chip
    above title) and the circulars line's «تعميم:» prefix.
    """
    if not rows:
        return []
    # n → chunk_id; keep only rows we can key.
    n_to_chunk: dict[int, str] = {}
    for r in rows:
        cid = _reg_chunk_id(r)
        if cid:
            n_to_chunk[int(r["n"])] = cid

    chunk_rows = _select_in(
        supabase, _CHUNKS_TABLE, "id, title, regulation_id", "id",
        list(n_to_chunk.values()),
    )
    chunk_by_id = {str(c["id"]): c for c in chunk_rows if c.get("id")}

    reg_ids = [
        str(c["regulation_id"]) for c in chunk_rows if c.get("regulation_id")
    ]
    # doc_type_raw rides along on the join the line already pays for — the chip
    # costs one extra column, not one extra round-trip.
    reg_rows = _select_in(
        supabase, _REGS_TABLE, "id, clean_title, title, doc_type_raw", "id", reg_ids,
    )
    reg_by_id = {str(g["id"]): g for g in reg_rows if g.get("id")}

    lines: list[SourceLine] = []
    for r in rows:
        n = int(r["n"])
        chunk = chunk_by_id.get(n_to_chunk.get(n, ""))
        if not chunk:
            lines.append(SourceLine(n=n, text=_FALLBACK_LINE, domain="regulations"))
            continue
        chunk_title = _cap(chunk.get("title") or "", snippet_max_chars)
        reg = reg_by_id.get(str(chunk.get("regulation_id") or ""))
        reg_name = ""
        doc_type = _reg_type_label(None)
        if reg:
            reg_name = _cap(
                reg.get("clean_title") or reg.get("title") or "", snippet_max_chars,
            )
            doc_type = _reg_type_label(reg.get("doc_type_raw"))
        descriptor = " — ".join(p for p in (reg_name, chunk_title) if p)
        # No descriptor → the bare stub. A chip with nothing to label is noise,
        # and the stub line is the invariant that keeps [n] present.
        text = f"{doc_type}: {descriptor}" if descriptor else _FALLBACK_LINE
        lines.append(SourceLine(n=n, text=text, domain="regulations"))
    return lines


def _resolve_cases(
    supabase, rows: list[dict], *, snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> list[SourceLine]:
    """Build ``[n] قضية: [{case_number}] {card title} — {card snippet}`` lines.

    ``item_id`` is ``cases.id`` (migration 050). Rows with a NULL item_id
    (legacy, pre-backfill) can't be resolved here — they degrade to a stub.
    (Live: 0 of 442 case ref rows have a NULL item_id, so the panel's
    ``case:``-ref_id join and this id join select the same row on every live row.)

    THE TWO FIXES (plan §2.3.1 divergences 2 and 3):

    * **Card title.** The panel does NOT title a ruling with its number — it
      derives the title from the summary and falls back to «حكم {court}»
      (``_build_case_shells`` docstring, references_service.py:513-551 →
      ``preprocessor._reference_from_ura:521``). A user quoting «حكم المحكمة
      التجارية» is quoting that fallback, which appeared nowhere in the old
      ``[{case_number}] {summary}`` line.
    * **Cap.** The old line emitted the FULL summary — p50 2,375 / p90 3,512
      chars on live cited rows, so eight case refs cost ~19–28k chars just to
      let a model pick one. The user's card never showed past 500, so nothing
      quotable is lost by matching that cap.

    ``[{case_number}]`` is kept even though the card omits it: it is an
    identifier the agent needs to fetch/quote the ruling, not body text the user
    could be reading — the cap governs body text.
    """
    if not rows:
        return []
    n_to_case = {int(r["n"]): str(r["item_id"]) for r in rows if r.get("item_id")}
    # short_summary / court / court_level join the select for the SAME reason
    # ura/enrich._CASE_COLS:163-170 carries them: they are the title-derivation
    # inputs (~200 chars) and the snippet's «المحكمة: …» header.
    case_rows = _select_in(
        supabase, _CASES_TABLE,
        "id, case_number, summary, short_summary, court, court_level", "id",
        list(n_to_case.values()),
    )
    case_by_id = {str(c["id"]): c for c in case_rows if c.get("id")}

    lines: list[SourceLine] = []
    for r in rows:
        n = int(r["n"])
        case = case_by_id.get(n_to_case.get(n, ""))
        if not case:
            lines.append(SourceLine(n=n, text=_FALLBACK_LINE, domain="cases"))
            continue
        number = (case.get("case_number") or "").strip()
        # strip_pipeline_sections: ~16.5k summaries end in the pipeline's
        # resolver-telemetry appendix (internal reg/chunk ids + scores) and 252
        # in the classifier's `ConnectError:` crash dump — this line lands in an
        # LLM tool result, which echoes both back to the user. The panel strips
        # at the same point (ura/enrich._enrich_cases:414-417 fills
        # ``case_content`` with the stripped summary), so title and snippet below
        # both read the stripped text, exactly like the card.
        summary = strip_pipeline_sections((case.get("summary") or "").strip())
        title = _cap(_case_card_title(case, summary), snippet_max_chars)
        snippet = _case_card_snippet(case, summary, snippet_max_chars)
        label = f"[{number}] " if number else ""
        head = f"{_DOMAIN_TYPE_LABELS['cases']}: {label}{title}".strip()
        text = f"{head} — {snippet}" if snippet else head
        lines.append(SourceLine(n=n, text=text.strip() or _FALLBACK_LINE, domain="cases"))
    return lines


def _case_card_title(case: dict, summary: str) -> str:
    """The المراجع card's OWN title for a ruling — «حكم {court}» fallback included.

    Reimplementation of the backend panel path, which cannot be imported from
    ``agents/``: ``references_service._build_case_shells`` (:513-551) rebuilds a
    ``CaseURAResult`` from nothing but a ``ref_id``, ``ura/enrich._enrich_cases``
    fills ``short_summary`` / ``court`` / ``case_content``, and
    ``preprocessor._reference_from_ura`` (:521) titles the card with
    ``judgment_subject(...) or "قضية"``. ``judgment_subject`` itself is imported,
    never re-derived — it cut the 10,000 published /judgments slugs, so a forked
    copy would title a card differently from the page its button opens.

    ``case_number`` / ``judgment_number`` are deliberately NOT passed. The panel
    path never fills them (``_CASE_COLS`` does not select either), so its
    fallback renders «حكم {court}» — passing the number here would print «حكم
    {court} رقم {number}», a string the user never saw.
    """
    return judgment_subject(
        {
            "short_summary": case.get("short_summary") or "",
            "summary": summary,
            "court": (case.get("court") or "").strip(),
        }
    ) or "قضية"


def _case_card_snippet(case: dict, summary: str, max_chars: int) -> str:
    """The المراجع card's OWN snippet for a ruling, capped exactly as the card is.

    ``references_service._load_references:298`` builds every non-compliance
    card's snippet with ``build_snippet(shell)``; for a ruling that renders
    «المحكمة: {court} ({level})» + the stripped summary and then truncates on a
    line/sentence boundary. Rebuilding the shell here and calling that SAME
    function — rather than re-truncating the summary by hand — is what keeps the
    two renderings byte-comparable.

    ``relevance`` is a required field on the shell and is not read by the snippet
    path, so the constant is deliberate, not a guess. ``referenced_regulations``
    is NOT fetched: it only reaches the card's snippet when the court header plus
    the whole summary is shorter than the cap (rare — p50 summary is 2,375
    chars), and fetching that JSONB per ref would cost real egress for a tail.
    Never raises: a shell that fails validation falls back to a plain cap, since
    a preview line must not be able to break a tool read.
    """
    try:
        shell = CaseURAResult(
            ref_id="",
            source_type="case",
            relevance="medium",
            case_content=summary,
            court=(case.get("court") or "").strip(),
            court_level=(case.get("court_level") or "").strip(),
        )
        return _one_line(build_snippet(shell, max_chars=max_chars))
    except Exception:  # noqa: BLE001
        logger.debug("unfold: case snippet build failed — capping raw summary", exc_info=True)
        return _cap(summary, max_chars)


def _resolve_compliance(
    supabase, rows: list[dict], *, snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> list[SourceLine]:
    """Build ``[n] {service name}`` lines.

    ``item_id`` is ``services.id`` (migration 050). NULL item_id → stub.

    NAME-ONLY, on purpose: the panel blanks a service card's snippet outright
    (``references_service.py:298``) because ``services.service_name_ar`` is
    «{الجهة} - {اسم الخدمة}» on all 4,717 rows — the name already carries both
    halves of the card. There is no body string here to bound.
    """
    if not rows:
        return []
    n_to_service = {int(r["n"]): str(r["item_id"]) for r in rows if r.get("item_id")}
    svc_rows = _select_in(
        supabase, _SERVICES_TABLE, "id, service_name_ar", "id",
        list(n_to_service.values()),
    )
    svc_by_id = {str(s["id"]): s for s in svc_rows if s.get("id")}

    lines: list[SourceLine] = []
    for r in rows:
        n = int(r["n"])
        svc = svc_by_id.get(n_to_service.get(n, ""))
        name = _cap(svc.get("service_name_ar") or "", snippet_max_chars) if svc else ""
        lines.append(SourceLine(n=n, text=name or _FALLBACK_LINE, domain="compliance"))
    return lines


def _circular_id(row: dict) -> str:
    """``circulars.id`` for a circulars ref row.

    Prefers the ``item_id`` UUID (persist mints it from the ``circular:<uuid>``
    ref_id); falls back to stripping the ``circular:`` prefix off ``ref_id`` (the
    circular ref_id IS the circulars.id uuid). Mirrors :func:`_reg_chunk_id`.
    """
    item_id = row.get("item_id")
    if item_id:
        return str(item_id)
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith("circular:"):
        return ref_id[len("circular:"):]
    return ""


def _circular_entity_name(row: dict) -> str:
    """Issuing entity name from a fetched circular row.

    The ``entities`` name is embedded via the ``circulars_entity_id_fkey`` FK, so
    it arrives nested under ``row["entities"]`` — a to-one embed (object), but a
    list is tolerated defensively. Mirrors reg_search's ``_circular_entity_name``.
    """
    ent = row.get("entities")
    if isinstance(ent, dict):
        return (ent.get("entity_name") or "").strip()
    if isinstance(ent, list) and ent and isinstance(ent[0], dict):
        return (ent[0].get("entity_name") or "").strip()
    return ""


def _resolve_circulars(
    supabase, rows: list[dict], *, snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> list[SourceLine]:
    """Build ``[n] تعميم: {title} — {entity name}`` lines.

    ``item_id`` is ``circulars.id``; a NULL item_id falls back to the
    ``circular:<uuid>`` ref_id (mirrors the regulations resolver). One batched
    ``circulars`` fetch pulls the title + the embedded issuing entity name. A row
    whose circular can't be resolved degrades to a stub so its ``[n]`` is never
    silently lost.

    Already card-shaped: «تعميم» IS the panel's chip for this domain
    (``DOMAIN_META`` in ReferencePanel.tsx:130-134), the title is the card's
    label and the entity is its ``regulation_title`` slot. Both strings are
    capped for the same reason the others are — a card string can only be as
    long as the card showed.
    """
    if not rows:
        return []
    n_to_circ: dict[int, str] = {}
    for r in rows:
        cid = _circular_id(r)
        if cid:
            n_to_circ[int(r["n"])] = cid

    circ_rows = _select_in(
        supabase, _CIRCULARS_TABLE,
        "id, title, entities!circulars_entity_id_fkey(entity_name)", "id",
        list(n_to_circ.values()),
    )
    circ_by_id = {str(c["id"]): c for c in circ_rows if c.get("id")}

    lines: list[SourceLine] = []
    for r in rows:
        n = int(r["n"])
        circ = circ_by_id.get(n_to_circ.get(n, ""))
        if not circ:
            lines.append(SourceLine(n=n, text=_FALLBACK_LINE, domain="circulars"))
            continue
        title = _cap(circ.get("title") or "", snippet_max_chars)
        entity = _cap(_circular_entity_name(circ), snippet_max_chars)
        chip = _DOMAIN_TYPE_LABELS["circulars"]
        head = f"{chip}: {title}" if title else chip
        text = f"{head} — {entity}" if entity else head
        lines.append(SourceLine(n=n, text=text, domain="circulars"))
    return lines


def resolve_used_sources(
    supabase, wi_id: str, *, snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> list[SourceLine]:
    """Resolve the used-only citation manifest for a WI, sorted by ``n``.

    Groups the used ref rows by domain and dispatches the four lean per-domain
    resolvers. Never raises — a resolver hiccup contributes no lines for that
    domain rather than failing the whole read.

    Args:
        snippet_max_chars: the cap every card-derived string is truncated to.
            Defaults to the card's own 500 (:data:`_SNIPPET_MAX_CHARS`). A
            preview caller that wants a cheaper identification pass tightens it
            HERE rather than forking the formatters.
    """
    refs = _fetch_used_refs(supabase, wi_id)
    if not refs:
        return []
    by_domain: dict[str, list[dict]] = {
        "regulations": [], "cases": [], "compliance": [], "circulars": [],
    }
    for r in refs:
        dom = r.get("domain")
        if dom in by_domain:
            by_domain[dom].append(r)
        else:
            logger.warning("unfold: unknown ref domain %r — skipping", dom)

    cap = snippet_max_chars
    lines: list[SourceLine] = []
    lines.extend(_resolve_regulations(supabase, by_domain["regulations"], snippet_max_chars=cap))
    lines.extend(_resolve_cases(supabase, by_domain["cases"], snippet_max_chars=cap))
    lines.extend(_resolve_compliance(supabase, by_domain["compliance"], snippet_max_chars=cap))
    lines.extend(_resolve_circulars(supabase, by_domain["circulars"], snippet_max_chars=cap))
    return sorted(lines, key=lambda x: x.n)


def unfold_item(
    supabase, item_id: str, user_id: str, *,
    snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> str:
    """Full deterministic unfold: ``content_md`` + used-only citation manifest.

    Returns the rendered markdown string, or ``""`` when the item is missing /
    out of the user's scope (same silent-skip contract as the old
    ``read_workspace_item`` — the LLM moves on without retrying).

    Blocking: every read here goes through the SYNC supabase client, so an async
    caller must run this in a worker thread (the tool below does).
    """
    item = _fetch_item(supabase, item_id, user_id)
    if item is None:
        return ""
    content_md = item.get("content_md") or ""
    lines = resolve_used_sources(supabase, item_id, snippet_max_chars=snippet_max_chars)
    return render_unfold_md(content_md, lines)


def _encode_unfold_output(supabase, user_id: str, content: str) -> str:
    """Encode the LLM-bound unfold output via the active turn codec + persist.

    وضع السرية seam: mask identifiers/emails in the rendered content before it
    feeds an LLM, then persist any newly-minted fakes synchronously (a
    pause/resume in a fresh process reloads the codec from the DB). A None codec
    (no masked turn) or a disabled codec is a byte-identical passthrough. Never
    raises — masking must not break a tool read.
    """
    if not content:
        return content
    from backend.app.services.masking_service import active_codec, persist_new_mappings

    codec = active_codec()
    if codec is None:
        return content
    try:
        encoded = codec.encode(content)
    except Exception:  # noqa: BLE001
        logger.debug("unfold: encode failed — returning raw content", exc_info=True)
        return content
    if user_id:
        persist_new_mappings(supabase, user_id, codec)
    return encoded


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #


def register_unfold_workspace_item(agent: Agent) -> None:
    """Register the ``unfold_workspace_item`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasWorkspaceContext`
    (``.supabase`` + ``.user_id`` + ``.wi_alias_map``). Replaces
    ``read_workspace_item`` on the router and adds the read capability to the
    deep_search planner decider and writer_planner.
    """

    @agent.tool
    async def unfold_workspace_item(
        ctx: RunContext[HasWorkspaceContext],
        wi: str,
    ) -> str:
        """Return a workspace item's full content PLUS the named sources it cites.

        Use this when the per-item ``summary`` in your context isn't enough —
        e.g. answering a direct question about an item's contents, or when the
        user refers to a **specific named regulation / ruling / service** and
        you need to see which named sources a prior search actually cited.

        The result is the item's markdown body, followed by a list of every
        source it cites, keyed by the same ``[n]`` numbers that appear in the
        body::

            [1] لائحة: {regulation name} — {chunk title}
            [2] قضية: [{case number}] {ruling title} — {short excerpt}
            [3] {service name}
            [4] تعميم: {title} — {entity}

        Each line carries what the user's own المراجع card shows for that source
        — its Arabic type word (نظام / لائحة / دليل / قضية / تعميم), its title,
        and for a ruling a short excerpt — so a user naming a source by what is
        printed on their screen («اللائحة اللي في المراجع», «حكم المحكمة
        التجارية») can be matched line for line. Excerpts are deliberately short:
        they identify the source, they are not the source. To ANSWER from one,
        search or fetch it by the exact name on its line.

        Only sources actually used in the body appear. If a cited regulation
        matches the name the user is asking about, you can answer about it
        directly, or dispatch a focused search anchored on that exact name.

        Pass the ``WI-{n}`` alias shown in the workspace summaries (e.g.
        ``"WI-3"``). Can be called in parallel for several items at once.
        Returns ``""`` if the alias is unknown / inaccessible — in which case
        move on without retrying.

        Args:
            wi: The ``WI-{n}`` alias of the workspace item to unfold. A raw
                UUID is also accepted but always prefer the alias form.
        """
        item_id = _resolve_wi_alias(wi, getattr(ctx.deps, "wi_alias_map", {}) or {})
        if not item_id:
            logger.info("unfold_workspace_item: alias %r not resolvable", wi)
            return ""
        try:
            # Sync supabase client (the whole agents/ layer uses it) — the four
            # batched joins below would block the event loop, and this tool is
            # called in parallel across several WIs, so it runs off-thread.
            content = await asyncio.to_thread(
                unfold_item, ctx.deps.supabase, item_id, ctx.deps.user_id,
            )
            # وضع السرية: the rendered content_md + used-source manifest is
            # LLM-bound context (router / planner_decider / writer_planner). Mask
            # identifiers/emails before returning so the LLM never sees raw PII;
            # the stored content_md stays real. This also masks OCR'd attachment
            # content that flows through here. New fakes are persisted IMMEDIATELY
            # (a fresh-process resume reloads the codec from DB). Byte-identical
            # passthrough when masking is disabled / no turn codec is active.
            content = _encode_unfold_output(
                ctx.deps.supabase, getattr(ctx.deps, "user_id", ""), content
            )
            logger.info(
                "unfold_workspace_item: unfolded %s (alias %s) — %d chars",
                item_id, wi, len(content),
            )
            return content
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "unfold_workspace_item error for %s (alias %s): %s", item_id, wi, exc,
            )
            return ""


__all__ = [
    "register_unfold_workspace_item",
    "unfold_item",
    "resolve_used_sources",
    "render_unfold_md",
    "SourceLine",
    "HasWorkspaceContext",
]
