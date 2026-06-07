"""unfold_workspace_item — read a workspace item AND its used citations.

Replaces the old ``read_workspace_item`` tool. Where ``read_workspace_item``
returned only ``content_md``, this returns the body PLUS a used-only,
``n``-keyed manifest of every source cited inside it::

    [1] {regulation name} — {chunk title}      (regulations domain)
    [2] [{case number}] {case summary}          (cases domain)
    [3] {service name}                          (compliance domain)

The numbers match the ``[n]`` citation markers in ``content_md`` (the same
``workspace_item_references.n``), so an agent reading the item can map any
``[n]`` in the body to the exact named source. The content-only read could
not do this — which is why the router/planner kept failing to recognise a
user's reference to a *specific named regulation* (e.g. «نظام اشتراطات
المطاعم») that lived inside a prior search result.

Why a tool (not eager context): the manifest costs a couple of batched joins
per WI, so it loads on demand — the agent calls it when the per-item summary
in its context isn't enough.

Resolution layer (:func:`resolve_used_sources`, :func:`render_unfold_md`) is
pure / batched — title + summary only, no ``source_view`` or snippet building
— so it unit-tests without an agent or a live DB. Mirrors how the
item_analyzer's callers unfold ``workspace_item_references`` (used refs →
per-domain source rows), but title-only and deterministic (no LLM).

Registration::

    from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
    register_unfold_workspace_item(agent)   # deps must expose .supabase, .user_id, .wi_alias_map

The deps object must structurally satisfy :class:`HasWorkspaceContext`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from pydantic_ai import Agent, RunContext

logger = logging.getLogger(__name__)


# --- Schema config: a table/column rename is a one-line change here. ----------
_ITEMS_TABLE = "workspace_items"
_REFS_TABLE = "workspace_item_references"
_CHUNKS_TABLE = "chunks_v2"
_REGS_TABLE = "regulations_v2"
_CASES_TABLE = "cases"
_SERVICES_TABLE = "services"

# PostgREST `.in_()` batch size — matches references_service / enrich.py.
_ID_BATCH = 150

# Header for the citation manifest appended after content_md.
_MANIFEST_HEADER = "## المصادر المستخدمة في هذا العنصر"
_FALLBACK_LINE = "(مصدر غير متوفر)"


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


def _resolve_regulations(supabase, rows: list[dict]) -> list[SourceLine]:
    """Build ``[n] {regulation name} — {chunk title}`` lines.

    Two batched joins: chunks_v2 (id → title, regulation_id) then
    regulations_v2 (id → clean_title|title). A row whose chunk can't be
    resolved degrades to a stub line so its ``[n]`` is never silently lost.
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
    reg_rows = _select_in(
        supabase, _REGS_TABLE, "id, clean_title, title", "id", reg_ids,
    )
    reg_by_id = {str(g["id"]): g for g in reg_rows if g.get("id")}

    lines: list[SourceLine] = []
    for r in rows:
        n = int(r["n"])
        chunk = chunk_by_id.get(n_to_chunk.get(n, ""))
        if not chunk:
            lines.append(SourceLine(n=n, text=_FALLBACK_LINE, domain="regulations"))
            continue
        chunk_title = (chunk.get("title") or "").strip()
        reg = reg_by_id.get(str(chunk.get("regulation_id") or ""))
        reg_name = ""
        if reg:
            reg_name = (reg.get("clean_title") or reg.get("title") or "").strip()
        text = " — ".join(p for p in (reg_name, chunk_title) if p) or _FALLBACK_LINE
        lines.append(SourceLine(n=n, text=text, domain="regulations"))
    return lines


def _resolve_cases(supabase, rows: list[dict]) -> list[SourceLine]:
    """Build ``[n] [{case_number}] {summary}`` lines.

    ``item_id`` is ``cases.id`` (migration 050). Rows with a NULL item_id
    (legacy, pre-backfill) can't be resolved here — they degrade to a stub.
    """
    if not rows:
        return []
    n_to_case = {int(r["n"]): str(r["item_id"]) for r in rows if r.get("item_id")}
    case_rows = _select_in(
        supabase, _CASES_TABLE, "id, case_number, summary", "id",
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
        summary = (case.get("summary") or "").strip()
        label = f"[{number}] " if number else ""
        text = (label + summary).strip() or _FALLBACK_LINE
        lines.append(SourceLine(n=n, text=text, domain="cases"))
    return lines


def _resolve_compliance(supabase, rows: list[dict]) -> list[SourceLine]:
    """Build ``[n] {service name}`` lines.

    ``item_id`` is ``services.id`` (migration 050). NULL item_id → stub.
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
        name = (svc.get("service_name_ar") or "").strip() if svc else ""
        lines.append(SourceLine(n=n, text=name or _FALLBACK_LINE, domain="compliance"))
    return lines


def resolve_used_sources(supabase, wi_id: str) -> list[SourceLine]:
    """Resolve the used-only citation manifest for a WI, sorted by ``n``.

    Groups the used ref rows by domain and dispatches the three lean
    per-domain resolvers. Never raises — a resolver hiccup contributes no
    lines for that domain rather than failing the whole read.
    """
    refs = _fetch_used_refs(supabase, wi_id)
    if not refs:
        return []
    by_domain: dict[str, list[dict]] = {"regulations": [], "cases": [], "compliance": []}
    for r in refs:
        dom = r.get("domain")
        if dom in by_domain:
            by_domain[dom].append(r)
        else:
            logger.warning("unfold: unknown ref domain %r — skipping", dom)

    lines: list[SourceLine] = []
    lines.extend(_resolve_regulations(supabase, by_domain["regulations"]))
    lines.extend(_resolve_cases(supabase, by_domain["cases"]))
    lines.extend(_resolve_compliance(supabase, by_domain["compliance"]))
    return sorted(lines, key=lambda x: x.n)


def unfold_item(supabase, item_id: str, user_id: str) -> str:
    """Full deterministic unfold: ``content_md`` + used-only citation manifest.

    Returns the rendered markdown string, or ``""`` when the item is missing /
    out of the user's scope (same silent-skip contract as the old
    ``read_workspace_item`` — the LLM moves on without retrying).
    """
    item = _fetch_item(supabase, item_id, user_id)
    if item is None:
        return ""
    content_md = item.get("content_md") or ""
    lines = resolve_used_sources(supabase, item_id)
    return render_unfold_md(content_md, lines)


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
    async def unfold_workspace_item(  # noqa: RUF029 — supabase client is sync by design
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

            [1] {regulation name} — {chunk title}
            [2] [{case number}] {case summary}
            [3] {service name}

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
            content = unfold_item(ctx.deps.supabase, item_id, ctx.deps.user_id)
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
