"""Convert DB message rows to Pydantic AI ModelMessage list."""
from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
)


# Map a ``workspace_items.kind`` → the ``DispatchAgent.agent_family`` that
# produces it. Used to annotate assistant turns in the router's history with
# provenance, so the router can route a follow-up ("elaborate that letter")
# back to the SAME family with ``target_wi`` instead of mis-firing a fresh
# deep_search. Kinds not in the map render the tag without an agent_family hint.
_KIND_TO_FAMILY: dict[str, str] = {
    "agent_search": "deep_search",
    "agent_writing": "writing",
    "agent_writer": "writing",
    "note": "writing",
}


def build_provenance_tag(
    artifact_ids: list[str],
    wi_provenance: dict[str, tuple[int | None, str, str]],
) -> str:
    """Build a one-line Arabic provenance annotation for an assistant turn.

    Shared by the router's ``messages_to_history`` and the orchestrator's
    ``_load_recent_messages`` (the planners' recent-message window) so every
    agent that reads conversation history sees the SAME provenance marker.

    ``wi_provenance`` maps ``item_id`` → ``(wi_seq, kind, title)``. For each
    artifact this turn produced that is still resolvable to a ``WI-{seq}``
    alias, the tag names the alias, its **title** (so an agent can map a
    natural-language reference like "the letter" to the right WI), and — when
    the kind is known — the ``agent_family`` that produced it. Returns ``""``
    when nothing resolves — the turn then renders as plain text (e.g. a direct
    ChatResponse that created no artifact, which is itself the signal "this was
    a direct answer, not a specialist output").
    """
    entries: list[tuple[str, str]] = []  # (alias, title)
    families: set[str] = set()
    for aid in artifact_ids:
        prov = wi_provenance.get(str(aid))
        if not prov:
            continue
        wi_seq, kind, title = prov
        if wi_seq is None:
            continue
        entries.append((f"WI-{int(wi_seq)}", (title or "").strip()))
        fam = _KIND_TO_FAMILY.get(str(kind or ""))
        if fam:
            families.add(fam)
    if not entries:
        return ""
    fam_part = f" (agent_family={next(iter(families))})" if len(families) == 1 else ""
    rendered = "، ".join(
        f"{alias} «{title}»" if title else alias for alias, title in entries
    )
    return f"〔[نظام] أنتج هذا الردّ متخصصٌ{fam_part} وأنشأ العنصر {rendered}〕"


def messages_to_history(
    rows: list[dict],
    wi_provenance: dict[str, tuple[int | None, str, str]] | None = None,
) -> list[ModelMessage]:
    """
    Convert rows from the `messages` table into Pydantic AI message history.

    Each row must have at least `role` and `content` keys.
    - role="user"      → ModelRequest with UserPromptPart
    - role="assistant"  → ModelResponse with TextPart
    - role="system"     → ModelRequest with UserPromptPart (treated as context)

    Rows are expected to be ordered by created_at ASC.

    ``wi_provenance`` (optional) maps ``item_id`` → ``(wi_seq, kind, title)``.
    When provided, an assistant row carrying ``artifact_ids`` gets a one-line
    provenance tag prepended to its text (which agent produced the turn + which
    ``WI-{seq}`` + its title). This is the signal the router needs to recognise a
    follow-up that refines the just-produced artifact. When omitted, behaviour
    is identical to before (plain text, no tags) — only the router opts in.
    """
    history: list[ModelMessage] = []
    for row in rows:
        role = row.get("role", "")
        content = row.get("content", "")
        if not content:
            continue

        if role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            text = content
            if wi_provenance:
                artifact_ids = row.get("artifact_ids") or []
                if artifact_ids:
                    tag = build_provenance_tag(list(artifact_ids), wi_provenance)
                    if tag:
                        text = f"{tag}\n{content}"
            history.append(ModelResponse(parts=[TextPart(content=text)]))
        elif role == "system":
            # System-injected messages (e.g. task summaries) appear as assistant context
            history.append(ModelResponse(parts=[TextPart(content=content)]))

    return history
