"""edit_supabase_md — reliable snippet editing for Luna artifacts.

Edits the ``content_md`` text column of a ``workspace_items`` row using the most
reliable LLM edit primitive: **anchored exact-string replacement with a
mandatory uniqueness check**, plus a whitespace-tolerant fallback.

Why this method (full rationale in ``tool_repository/PLAN.md``):
  - The agent quotes verbatim text it can already see in the artifact instead of
    guessing a line number. Copying visible text is an LLM strength; counting
    lines is a weakness that drifts after any prior edit.
  - The tool verifies the quote is UNIQUE before writing, so a wrong-location
    edit is impossible — it refuses (via ModelRetry) rather than guessing.
  - A 3-step match ladder (exact → whitespace-normalized → fail-with-hint)
    absorbs the ~30 % of LLM quotes that drift on whitespace.
  - Optimistic concurrency (a version-token guard) is mandatory because
    ``agent_writing`` artifacts are co-edited by the user in the UI; a blind
    overwrite would clobber the user's in-flight change.

Registration::

    from agents.tool_repository.edit_supabase_md import register_edit_supabase_md
    register_edit_supabase_md(agent)   # agent.deps must expose `.supabase`

The matching engine (`locate`, `apply_edit`) is pure and dependency-free so it
can be unit-tested without a database or an agent.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic_ai import Agent, ModelRetry, RunContext


# --- Schema config: kept here so a table/column rename is a one-line change. ---
TABLE = "workspace_items"
ID_COL = "item_id"
CONTENT_COL = "content_md"
# Optimistic-lock token. Any value that changes on every write works
# (updated_at timestamp, an integer version column, or Postgres xmin).
VERSION_COL = "updated_at"


@runtime_checkable
class HasSupabase(Protocol):
    """Structural type for the agent deps — we only need a supabase client.

    Any concrete deps object (e.g. ``WriterPlannerDeps``) satisfies this as long
    as it has a ``.supabase`` attribute, so this module stays decoupled from the
    per-agent deps classes.
    """

    supabase: object  # supabase.Client — kept loose to avoid a hard import here


# --------------------------------------------------------------------------- #
# Matching engine — pure, dependency-free, unit-testable in isolation.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    how: str  # "exact" | "whitespace"


class MatchError(Exception):
    """Raised when ``old_text`` cannot be uniquely located.

    Carries an ``hint`` written for the LLM so the caller can turn it straight
    into a ``ModelRetry`` and let the model self-correct.
    """

    def __init__(self, hint: str):
        super().__init__(hint)
        self.hint = hint


def _whitespace_regex(needle: str) -> re.Pattern[str]:
    """Compile a regex matching ``needle`` with every whitespace run flexible.

    Each maximal run of whitespace becomes ``\\s+``; every other character is
    escaped literally. Works on Arabic / RTL text unchanged (re.escape is
    codepoint-based, and ``\\s+`` matches any Unicode whitespace).
    """
    parts = [p for p in re.split(r"\s+", needle.strip()) if p]
    return re.compile(r"\s+".join(re.escape(p) for p in parts))


def locate(content: str, old_text: str) -> Match:
    """Find the single span in ``content`` that ``old_text`` refers to.

    Ladder:
      1. exact substring match (must be unique)
      2. whitespace-normalized regex match (must be unique)
      3. raise ``MatchError`` with the closest near-match to guide a retry
    """
    if not old_text.strip():
        raise MatchError("old_text is empty — quote the exact text you want to replace.")

    # Step 1 — exact.
    n = content.count(old_text)
    if n == 1:
        i = content.index(old_text)
        return Match(i, i + len(old_text), "exact")
    if n > 1:
        raise MatchError(
            f"old_text appears {n} times — it must identify exactly one place. "
            f"Add a line above or below your quote until it is unique."
        )

    # Step 2 — whitespace-tolerant.
    pat = _whitespace_regex(old_text)
    hits = list(pat.finditer(content))
    if len(hits) == 1:
        m = hits[0]
        return Match(m.start(), m.end(), "whitespace")
    if len(hits) > 1:
        raise MatchError(
            f"old_text matches {len(hits)} places after normalizing whitespace — "
            f"add more surrounding context so it is unique."
        )

    # Step 3 — not found: surface the closest existing line to help the model.
    raise MatchError(_not_found_hint(content, old_text))


def _not_found_hint(content: str, old_text: str) -> str:
    first_line = next((ln for ln in old_text.splitlines() if ln.strip()), old_text)
    close = difflib.get_close_matches(first_line, content.splitlines(), n=1, cutoff=0.5)
    if close:
        return (
            "old_text not found in the artifact. The closest existing line is:\n"
            f"    {close[0]!r}\n"
            "Re-quote the text exactly as it appears (copy it verbatim)."
        )
    return (
        "old_text not found in the artifact. Re-read the current content and copy "
        "the exact text you want to replace, character-for-character."
    )


def apply_edit(content: str, old_text: str, new_text: str) -> tuple[str, Match]:
    """Return ``(new_content, match)``. Raises ``MatchError`` if not unique.

    Slicing by the match span (rather than ``str.replace``) makes exact and
    whitespace matches behave identically and guarantees a single replacement.
    """
    m = locate(content, old_text)
    new_content = content[: m.start] + new_text + content[m.end :]
    return new_content, m


def unified_diff(before: str, after: str, item_id: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{item_id}:before",
            tofile=f"{item_id}:after",
        )
    )


# --------------------------------------------------------------------------- #
# Supabase read-modify-write with optimistic concurrency.
# --------------------------------------------------------------------------- #
# NOTE: calls use the synchronous supabase client (matching the rest of the
# agents/ codebase). If this ever runs on a latency-sensitive event loop, wrap
# `_fetch`/`_write` in `anyio.to_thread.run_sync`.


def _fetch(supabase, item_id: str) -> tuple[str, object]:
    res = (
        supabase.table(TABLE)
        .select(f"{CONTENT_COL}, {VERSION_COL}")
        .eq(ID_COL, item_id)
        .single()
        .execute()
    )
    if not res.data:
        raise MatchError(f"Artifact {item_id} not found.")
    return res.data[CONTENT_COL] or "", res.data[VERSION_COL]


def _write(supabase, item_id: str, new_content: str, version_token) -> bool:
    """Write guarded by the version token. Returns False on a lost-update race.

    The ``.eq(VERSION_COL, version_token)`` clause means the UPDATE matches zero
    rows if anyone changed the row since ``_fetch`` read it — that is the signal
    to ask the model to re-read and retry instead of clobbering the other write.
    """
    res = (
        supabase.table(TABLE)
        .update({CONTENT_COL: new_content})
        .eq(ID_COL, item_id)
        .eq(VERSION_COL, version_token)
        .execute()
    )
    return bool(res.data)


# --------------------------------------------------------------------------- #
# Pydantic AI tool.
# --------------------------------------------------------------------------- #


def register_edit_supabase_md(agent: Agent) -> None:
    """Register the ``edit_supabase_md`` tool on a Pydantic AI agent.

    The agent's deps must structurally satisfy :class:`HasSupabase` (i.e. expose
    a ``.supabase`` attribute holding a ``supabase.Client``).
    """

    @agent.tool
    async def edit_supabase_md(  # noqa: RUF029 — supabase client is sync by design
        ctx: RunContext[HasSupabase],
        item_id: str,
        old_text: str,
        new_text: str,
        dry_run: bool = False,
    ) -> str:
        """Replace a verbatim snippet inside an artifact's markdown.

        Quote ``old_text`` EXACTLY as it appears in the artifact you are looking
        at — copy it character-for-character. It must identify exactly ONE place
        in the document. Do NOT pass line numbers. If your quote is not unique,
        include a line above or below it until it is.

        Args:
            item_id: The artifact (``workspace_items.item_id``) to edit.
            old_text: Verbatim text to find and replace. Must be unique.
            new_text: Replacement text. Use an empty string to delete the span.
            dry_run: If true, return the diff without writing.

        Returns:
            A short confirmation including a unified diff of the change.

        Raises:
            ModelRetry: when ``old_text`` is missing/ambiguous, or the artifact
                was changed by someone else since it was read (re-read & retry).
        """
        supabase = ctx.deps.supabase

        if old_text == new_text:
            return "No change: old_text and new_text are identical."

        try:
            content, version = _fetch(supabase, item_id)
            new_content, match = apply_edit(content, old_text, new_text)
        except MatchError as exc:
            raise ModelRetry(exc.hint) from exc

        diff = unified_diff(content, new_content, item_id)

        if dry_run:
            return f"DRY RUN ({match.how} match) — nothing written:\n{diff}"

        if not _write(supabase, item_id, new_content, version):
            raise ModelRetry(
                f"Artifact {item_id} changed since you read it (concurrent edit). "
                f"Re-read its current content and reissue the edit."
            )

        return f"Edited {item_id} ({match.how} match):\n{diff}"


__all__ = [
    "register_edit_supabase_md",
    "locate",
    "apply_edit",
    "unified_diff",
    "Match",
    "MatchError",
    "TABLE",
    "ID_COL",
    "CONTENT_COL",
    "VERSION_COL",
]
