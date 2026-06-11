"""edit_supabase_md â€” reliable snippet editing for Luna artifacts.

Edits the ``content_md`` text column of a ``workspace_items`` row using the most
reliable LLM edit primitive: **anchored exact-string replacement with a
mandatory uniqueness check**, plus a whitespace-tolerant fallback.

Why this method (full rationale in ``tool_repository/PLAN.md``):
  - The agent quotes verbatim text it can already see in the artifact instead of
    guessing a line number. Copying visible text is an LLM strength; counting
    lines is a weakness that drifts after any prior edit.
  - The tool verifies the quote is UNIQUE before writing, so a wrong-location
    edit is impossible â€” it refuses (via ModelRetry) rather than guessing.
  - A 3-step match ladder (exact â†’ whitespace-normalized â†’ fail-with-hint)
    absorbs the ~30 % of LLM quotes that drift on whitespace.
  - Optimistic concurrency (a version-token guard) is mandatory because
    ``agent_writing`` artifacts are co-edited by the user in the UI; a blind
    overwrite would clobber the user's in-flight change.
  - Edits are BATCHED: the tool takes a list of ``{old_text, new_text}`` pairs,
    every pair is located against ONE snapshot of the original content, and the
    batch is applied atomically (all-or-nothing, one guarded write). Per-pair
    failures are collected into a single hint so the model can fix the whole
    batch in ONE retry, and matches are applied by descending start offset so
    earlier edits never shift later spans. Overlapping spans (including two
    pairs resolving to the same span) are rejected.
  - The pre-edit content is snapshotted to ``prev_content_md`` in the same
    guarded UPDATE â€” a one-level undo, overwritten on each edit (column added
    by migration 068).

Registration::

    from agents.tool_repository.edit_supabase_md import register_edit_supabase_md
    register_edit_supabase_md(agent)   # agent.deps must expose `.supabase`

The matching engine (`locate`, `apply_edit`, `apply_edits`) is pure and
dependency-free (pydantic only supplies the ``EditPair`` arg model) so it can
be unit-tested without a database or an agent.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext


# --- Schema config: kept here so a table/column rename is a one-line change. ---
TABLE = "workspace_items"
ID_COL = "item_id"
CONTENT_COL = "content_md"
# Optimistic-lock token. Any value that changes on every write works
# (updated_at timestamp, an integer version column, or Postgres xmin).
VERSION_COL = "updated_at"
# Pre-edit snapshot written in the same guarded UPDATE as the new content.
# One-level undo, overwritten on each edit (added by migration 068).
PREV_CONTENT_COL = "prev_content_md"


@runtime_checkable
class HasSupabase(Protocol):
    """Structural type for the agent deps â€” we only need a supabase client.

    Any concrete deps object (e.g. ``WriterPlannerDeps``) satisfies this as long
    as it has a ``.supabase`` attribute, so this module stays decoupled from the
    per-agent deps classes.
    """

    supabase: object  # supabase.Client â€” kept loose to avoid a hard import here


# --------------------------------------------------------------------------- #
# Matching engine â€” pure, dependency-free, unit-testable in isolation.
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
        raise MatchError("old_text is empty â€” quote the exact text you want to replace.")

    # Step 1 â€” exact.
    n = content.count(old_text)
    if n == 1:
        i = content.index(old_text)
        return Match(i, i + len(old_text), "exact")
    if n > 1:
        raise MatchError(
            f"old_text appears {n} times â€” it must identify exactly one place. "
            f"Add a line above or below your quote until it is unique."
        )

    # Step 2 â€” whitespace-tolerant.
    pat = _whitespace_regex(old_text)
    hits = list(pat.finditer(content))
    if len(hits) == 1:
        m = hits[0]
        return Match(m.start(), m.end(), "whitespace")
    if len(hits) > 1:
        raise MatchError(
            f"old_text matches {len(hits)} places after normalizing whitespace â€” "
            f"add more surrounding context so it is unique."
        )

    # Step 3 â€” not found: surface the closest existing line to help the model.
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


class EditPair(BaseModel):
    """One ``old_text â†’ new_text`` replacement inside a batch."""

    old_text: str
    new_text: str


def _as_pair(pair: EditPair | Sequence[str]) -> tuple[str, str]:
    """Normalize an :class:`EditPair` or a ``(old, new)`` 2-sequence."""
    if isinstance(pair, EditPair):
        return pair.old_text, pair.new_text
    old_text, new_text = pair
    return old_text, new_text


def _quote(text: str, limit: int = 40) -> str:
    """Short verbatim excerpt of a pair's old_text for error messages."""
    flat = " ".join(text.split())
    return repr(flat if len(flat) <= limit else flat[: limit - 1] + "â€¦")


def apply_edits(
    content: str,
    pairs: Sequence[EditPair] | Sequence[Sequence[str]],
) -> tuple[str, list[Match]]:
    """Apply a batch of edits atomically against ONE content snapshot.

    Every pair is located against the ORIGINAL ``content`` (so quotes never
    have to anticipate earlier edits in the same batch), then:

      - ALL location failures are collected and raised as ONE ``MatchError``
        whose hint lists each failing pair index (1-based) with its specific
        hint â€” the model fixes everything in a single retry.
      - Overlapping spans â€” including two pairs resolving to the SAME span
        (e.g. duplicate old_text) â€” are rejected, naming the colliding pairs.
      - Matches are applied sorted by start DESCENDING so earlier offsets
        never shift.

    Returns ``(new_content, matches)`` with ``matches`` in the original pair
    order. Raises ``MatchError`` on an empty batch, any location failure, or
    any overlap â€” in which case nothing is applied (all-or-nothing).
    """
    normalized = [_as_pair(p) for p in pairs]
    if not normalized:
        raise MatchError(
            "edits is empty â€” pass at least one {old_text, new_text} pair."
        )

    # Phase 1 â€” locate every pair against the original snapshot, collecting
    # ALL failures so the model can fix the whole batch in one retry.
    matches: list[Match | None] = []
    failures: list[str] = []
    for idx, (old_text, _new_text) in enumerate(normalized, start=1):
        try:
            matches.append(locate(content, old_text))
        except MatchError as exc:
            matches.append(None)
            failures.append(f"  - edit {idx} (old_text {_quote(old_text)}): {exc.hint}")
    if failures:
        raise MatchError(
            f"{len(failures)} of {len(normalized)} edits failed to locate. "
            "Nothing was changed â€” the batch is all-or-nothing. Fix every edit "
            "below and resend the FULL batch:\n" + "\n".join(failures)
        )
    located: list[Match] = matches  # type: ignore[assignment]  # all non-None here

    # Phase 2 â€” reject overlapping spans (incl. two pairs on the same span).
    by_start = sorted(range(len(located)), key=lambda k: (located[k].start, located[k].end))
    collisions: list[tuple[int, int]] = []
    frontier_idx = by_start[0]
    for k in by_start[1:]:
        if located[k].start < located[frontier_idx].end:
            collisions.append((frontier_idx + 1, k + 1))
        if located[k].end > located[frontier_idx].end:
            frontier_idx = k
    if collisions:
        named = "; ".join(
            f"edit {a} (old_text {_quote(normalized[a - 1][0])}) and "
            f"edit {b} (old_text {_quote(normalized[b - 1][0])})"
            for a, b in collisions
        )
        raise MatchError(
            "Overlapping edits â€” these pairs resolve to overlapping (or identical) "
            f"spans of the document: {named}. Nothing was changed. Make each "
            "old_text target a distinct, non-overlapping region and resend the "
            "FULL batch."
        )

    # Phase 3 â€” apply by start DESCENDING so earlier offsets never shift.
    new_content = content
    for k in sorted(range(len(located)), key=lambda i: located[i].start, reverse=True):
        m = located[k]
        new_content = new_content[: m.start] + normalized[k][1] + new_content[m.end :]
    return new_content, located


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


def _write(supabase, item_id: str, new_content: str, version_token, prev_content: str) -> bool:
    """Write guarded by the version token. Returns False on a lost-update race.

    The ``.eq(VERSION_COL, version_token)`` clause means the UPDATE matches zero
    rows if anyone changed the row since ``_fetch`` read it â€” that is the signal
    to ask the model to re-read and retry instead of clobbering the other write.

    The pre-edit ``prev_content`` is snapshotted to ``PREV_CONTENT_COL`` in the
    SAME guarded UPDATE, so the snapshot and the new content can never diverge
    (one-level undo, overwritten on each edit).
    """
    res = (
        supabase.table(TABLE)
        .update({CONTENT_COL: new_content, PREV_CONTENT_COL: prev_content})
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
    async def edit_supabase_md(  # noqa: RUF029 â€” supabase client is sync by design
        ctx: RunContext[HasSupabase],
        item_id: str,
        edits: list[EditPair],
        dry_run: bool = False,
    ) -> str:
        """Apply a batch of verbatim snippet replacements to an artifact's markdown.

        Pass ALL the edits the task needs in ONE call â€” the batch is atomic
        (all-or-nothing): every pair is located against the same snapshot of the
        document, and either every edit is applied in a single write or nothing
        changes at all.

        For each pair, quote ``old_text`` EXACTLY as it appears in the artifact
        you are looking at â€” copy it character-for-character. Each ``old_text``
        must identify exactly ONE place in the document. Do NOT pass line
        numbers. If a quote is not unique, include a line above or below it
        until it is. Quote against the ORIGINAL document â€” never against the
        result of another edit in the same batch â€” and make sure no two pairs
        touch overlapping text.

        Args:
            item_id: The artifact (``workspace_items.item_id``) to edit.
            edits: List of ``{old_text, new_text}`` pairs. ``old_text`` is
                verbatim text to find and replace (must be unique);
                ``new_text`` is the replacement â€” use an empty string to
                delete the span.
            dry_run: If true, return the diff without writing.

        Returns:
            A short confirmation including a unified diff of the combined
            change and how many edits were applied.

        Raises:
            ModelRetry: when any ``old_text`` is missing/ambiguous (the hint
                lists EVERY failing pair â€” fix them all and resend the full
                batch), when two pairs overlap, or when the artifact was
                changed by someone else since it was read (re-read & retry).
        """
        supabase = ctx.deps.supabase

        # Skip no-op pairs; if everything is a no-op there is nothing to do.
        pairs = [p for p in edits if p.old_text != p.new_text]
        if edits and not pairs:
            return "No change: old_text and new_text are identical."

        try:
            content, version = _fetch(supabase, item_id)
            new_content, matches = apply_edits(content, pairs)
        except MatchError as exc:
            raise ModelRetry(exc.hint) from exc

        diff = unified_diff(content, new_content, item_id)
        how_counts = Counter(m.how for m in matches)
        summary = (
            f"{len(matches)} edits: "
            + ", ".join(f"{how}Ã—{n}" for how, n in sorted(how_counts.items()))
        )

        if dry_run:
            return f"DRY RUN ({summary}) â€” nothing written:\n{diff}"

        if not _write(supabase, item_id, new_content, version, prev_content=content):
            raise ModelRetry(
                f"Artifact {item_id} changed since you read it (concurrent edit). "
                f"Re-read its current content and reissue the edit."
            )

        return f"Edited {item_id} ({summary}):\n{diff}"


__all__ = [
    "register_edit_supabase_md",
    "locate",
    "apply_edit",
    "apply_edits",
    "unified_diff",
    "EditPair",
    "Match",
    "MatchError",
    "TABLE",
    "ID_COL",
    "CONTENT_COL",
    "VERSION_COL",
    "PREV_CONTENT_COL",
]
