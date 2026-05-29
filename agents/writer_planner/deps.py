"""Runtime deps for the writer_planner decider.

Mirrors ``agents/deep_search_v4/planner/deps.py`` — Pydantic AI-style
dataclass carrying infra (Supabase, http_client) plus per-turn comprehension
inputs (intent, recent messages, attached items, prior artifacts as summary
views) plus mutable loop state (present_count, last_analyzer_output).

**Invariant — ``WriterPlannerDeps`` is never persisted and never survives a
pause.** It is rebuilt fresh by :func:`build_writer_planner_deps` on every
entry, including the resume path. Only ``agent_runs.message_history`` (the
decider's bytes) crosses the pause boundary. On resume, the orchestrator
re-loads attached_items + prior_artifacts + recent_messages, and
``last_analyzer_output`` starts fresh — the planner re-issues analyze_items
calls if needed.

**Core invariant — see writer_planner.md § Core invariant** — this deps shape
intentionally does NOT expose any ``content_md`` field. The planner LLM
works from summaries only; raw content reads go through the item_analyzer
(verdict path) or are fetched by the runner post-LLM (bypass path).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents.models import ChatMessageSnapshot, WorkspaceItemSnapshot
from agents.writer.models import WriterStyle
from backend.app.services.writer_planner_context import ArtifactSummaryView

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from supabase import Client as SupabaseClient

    from agents.memory.item_analyzer import AnalyzeOutput


@dataclass
class WriterPlannerDeps:
    """Per-turn state for the writer_planner decider.

    Built fresh every turn by :func:`build_writer_planner_deps`. The
    ``last_analyzer_output`` / ``present_count`` fields are mutable loop state
    populated by the tools during the agent's run; they exist on deps because
    tools need somewhere to stash structured side-effects that the runner
    consults after the LLM emits its final ``PlannerDecision``.
    """

    # --- immutable infrastructure ----------------------------------------
    supabase: "SupabaseClient"
    http_client: "httpx.AsyncClient | None" = None

    # --- scope identifiers (RLS + multi-tenant) --------------------------
    user_id: str = ""
    conversation_id: str = ""
    message_id: str | None = None
    turn_number: int = 0

    # --- per-turn comprehension inputs (orchestrator-hydrated) -----------
    # `intent` is the raw current user message; the planner LLM parses it
    # for stated subtype / parameters and crystallizes a distilled
    # `intent_ar` into the final PlannerDecision.
    intent: str = ""
    recent_messages: list[ChatMessageSnapshot] = field(default_factory=list)
    case_brief: str | None = None

    # Router-selected workspace_items for THIS turn. The planner sees
    # (item_id, kind, title, summary, word_count) — never content_md.
    attached_items: list[WorkspaceItemSnapshot] = field(default_factory=list)

    # Conversation-scope summary views (newest first). Loaded by
    # writer_planner_context.load_writer_planner_context — summary-only by
    # construction (core invariant).
    prior_artifacts: list[ArtifactSummaryView] = field(default_factory=list)

    # User preferences resolved from user_preferences.
    style: WriterStyle = field(default_factory=WriterStyle)

    # Migration 052 / agent communication protocol: per-conversation
    # ``WI-{seq} → item_id`` alias map. Built once per turn from
    # ``attached_items`` + ``prior_artifacts`` after both are hydrated. The
    # planner LLM emits WI-{seq} aliases everywhere (selected_wis,
    # role_assignments keys, analyze_items targeted_wi); the runner resolves
    # them back to UUIDs before invoking walkers / the analyzer. Items
    # without a ``wi_seq`` (case-only / system items) are intentionally
    # absent from the map — they are unreachable from the planner anyway.
    wi_alias_map: dict[int, str] = field(default_factory=dict)

    # --- mutable loop state ---------------------------------------------
    # Incremented by the present_plan_for_approval tool. Hard cap = 3; the
    # 4th call auto-approves with the most recent plan_md without pausing.
    present_count: int = 0
    # Stash set by the analyze_items tool on each call. The runner reads
    # the LAST value when the planner emits PlannerDecision(analyzer_invoked=True)
    # and walks verdicts to build AnalyzedItems.
    last_analyzer_output: "AnalyzeOutput | None" = None

    # --- SSE event sink --------------------------------------------------
    emit_sse: Callable[[dict], None] | None = None
    _events: list[dict] = field(default_factory=list)

    # -- alias resolver ----------------------------------------------------
    def resolve_wi_alias(self, alias: str) -> str | None:
        """Resolve a ``"WI-{n}"`` alias → workspace_items.item_id UUID.

        Accepts a raw UUID verbatim (defence-in-depth for older callers
        and runner stages that already hold UUIDs). Returns ``None`` if
        the alias is empty / malformed / unknown.

        See ``.claude/plans/agent_communication_protocol.md`` § Resolution
        for the canonical contract.
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
            return self.wi_alias_map.get(seq)
        if _UUID_RE.match(s):
            return s
        return None

    # -- tracking hooks (agents/utils/tracking.py protocol) ------------------
    def tracking_input(self) -> dict[str, Any]:
        """Bounded view of what the planner saw — span attrs ``input.*``."""
        return {
            "intent_chars": len(self.intent or ""),
            "attached_items": [s.item_id for s in self.attached_items],
            "prior_artifacts": len(self.prior_artifacts),
            "recent_messages": len(self.recent_messages),
            "case_brief_present": self.case_brief is not None,
            "detail_level": self.style.detail_level,
            "present_count": self.present_count,
        }

    def tracking_input_full(self) -> dict[str, Any]:
        """Verbatim view — env-gated span event only."""
        return {
            "intent": self.intent,
            "recent_messages": [getattr(m, "text", str(m)) for m in self.recent_messages],
            "attached_items": [
                {
                    "item_id": s.item_id,
                    "kind": getattr(s, "kind", None),
                    "title": getattr(s, "title", None),
                    "summary": getattr(s, "summary", None),
                }
                for s in self.attached_items
            ],
            "prior_artifacts": [getattr(a, "summary", str(a)) for a in self.prior_artifacts],
            "case_brief": self.case_brief,
        }


# Compiled at module level so they don't get rebuilt on every call.
_WI_ALIAS_RE = re.compile(r"^WI-(\d+)$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _compute_wi_alias_map(
    attached_items: list[WorkspaceItemSnapshot],
    prior_artifacts: list[ArtifactSummaryView],
) -> dict[int, str]:
    """Merge ``wi_seq → item_id`` across attached_items + prior_artifacts.

    Items without a ``wi_seq`` (case-only / pre-migration-052 rows) skip
    the map — they are unreachable from the planner's alias surface so
    the LLM can't legally emit a reference to them. When the same
    ``wi_seq`` appears in both lists, ``attached_items`` wins (it's the
    router-selected set for THIS turn and is canonical).
    """
    out: dict[int, str] = {}
    for prior in prior_artifacts:
        seq = getattr(prior, "wi_seq", None)
        item_id = getattr(prior, "item_id", "") or ""
        if seq is not None and item_id:
            out[int(seq)] = str(item_id)
    # attached_items overwrite any duplicate from prior_artifacts.
    for snap in attached_items:
        seq = getattr(snap, "wi_seq", None)
        item_id = getattr(snap, "item_id", "") or ""
        if seq is not None and item_id:
            out[int(seq)] = str(item_id)
    return out


def build_writer_planner_deps(
    *,
    supabase: "SupabaseClient",
    http_client: "httpx.AsyncClient | None" = None,
    user_id: str = "",
    conversation_id: str = "",
    message_id: str | None = None,
    turn_number: int = 0,
    intent: str = "",
    recent_messages: list[ChatMessageSnapshot] | None = None,
    case_brief: str | None = None,
    attached_items: list[WorkspaceItemSnapshot] | None = None,
    prior_artifacts: list[ArtifactSummaryView] | None = None,
    style: WriterStyle | None = None,
    emit_sse: Callable[[dict], None] | None = None,
) -> WriterPlannerDeps:
    """Construct a fresh :class:`WriterPlannerDeps`.

    Called on every entry — fresh dispatch AND resume. The resume path
    opens a new ``httpx.AsyncClient`` and calls this builder fresh; deps
    are never reused across a pause (see module-level invariant).
    """
    attached_list = list(attached_items or [])
    prior_list = list(prior_artifacts or [])
    return WriterPlannerDeps(
        supabase=supabase,
        http_client=http_client,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        turn_number=turn_number,
        intent=intent or "",
        recent_messages=list(recent_messages or []),
        case_brief=case_brief,
        attached_items=attached_list,
        prior_artifacts=prior_list,
        wi_alias_map=_compute_wi_alias_map(attached_list, prior_list),
        style=style or WriterStyle(),
        emit_sse=emit_sse,
    )


__all__ = ["WriterPlannerDeps", "build_writer_planner_deps"]
