"""The three read tools: ``find``, ``read_sections``, ``read_pages`` (plan §2.2).

Each returns slices of the file's masked text (``FileDoc.view.text``) and marks
what it returned as seen — that is what a quote may come from. No database: all
of it was loaded before the run. Errors come back as plain lines, never
``ModelRetry`` (a wrong ref should not cost an output retry).
"""
from __future__ import annotations

import html
import re
from typing import Any

from pydantic_ai import Agent, RunContext

from agents.tool_repository.citation_scan import find_folded, fold

from .context import section_line
from .deps import AttachmentDeps, FileDoc, SectionRow

#: Hits one ``find`` returns; past it, whole-word hits are kept first.
FIND_MAX_HITS = 20
#: The passage around a hit.
PASSAGE_CHARS = 300
#: Text one ``read_sections`` / ``read_pages`` call returns.
READ_MAX_CHARS = 40_000
#: Sections one range ref (``F1·S3–S40``) may expand to.
MAX_RANGE_SECTIONS = 40
#: How far a passage edge moves to land on a space.
_SNAP = 30

_SEP = r"[\s.·‧•・\-_/:|,]*"
_REF_RE = re.compile(
    rf"^\s*\[?\s*(?:(?:F\s*0*(?P<f>\d+)|WI\s*[-_ ]?\s*0*(?P<wi>\d+)){_SEP})?"
    rf"S\s*0*(?P<s>\d+)(?:\s*[–—\-]\s*(?:S\s*)?0*(?P<to>\d+))?\s*\]?\s*$",
    re.IGNORECASE,
)


def _files_line(files: list[FileDoc]) -> str:
    return ", ".join(f.label for f in files)


def _part_of(f: FileDoc, masked_pos: int) -> int:
    for n, (start, end) in enumerate(f.parts, start=1):
        if start <= masked_pos < end:
            return n
    return len(f.parts)


def where(f: FileDoc, masked_pos: int) -> str:
    """``F1·S4 · p 3`` — the section and page a masked offset sits in."""
    pos = f.view.to_orig(masked_pos)
    section = f.section_at(pos)
    parts = [f.ref(section) if section else f.label]
    page = f.page_at(pos)
    if page is not None:
        parts.append(f"p {page}")
    elif f.parts:
        parts.append(f"part {_part_of(f, masked_pos)} of {len(f.parts)}")
    return " · ".join(parts)


def _window(text: str, start: int, end: int) -> tuple[int, int]:
    pad = max(60, (PASSAGE_CHARS - (end - start)) // 2)
    a, b = max(0, start - pad), min(len(text), end + pad)
    if a > 0:
        space = text.find(" ", a, min(a + _SNAP, start))
        a = space + 1 if space >= 0 else a
    if b < len(text):
        space = text.rfind(" ", max(b - _SNAP, end), b)
        b = space if space >= 0 else b
    return a, b


def _targets(deps: AttachmentDeps, files: list[str] | None) -> tuple[list[FileDoc], list[str]]:
    if not files:
        return list(deps.files.values()), []
    found: list[FileDoc] = []
    unknown: list[str] = []
    for ref in files:
        f = deps.file(ref)
        if f is None:
            unknown.append(str(ref))
        elif f not in found:
            found.append(f)
    return found, unknown


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def find_text(deps: AttachmentDeps, phrase: str, files: list[str] | None = None) -> str:
    phrase = html.unescape(str(phrase or "")).strip()
    if not fold(phrase).split():
        return "Give a word or a phrase to search for."
    targets, unknown = _targets(deps, files)
    if not targets:
        return f"Unknown file(s): {', '.join(unknown)}. Files: {_files_line(list(deps.files.values()))}."
    deps.searched.append(phrase)

    hits: list[tuple[FileDoc, int, int, bool]] = []
    for f in targets:
        hits.extend((f, s, e, whole) for s, e, whole in find_folded(f.view.text, phrase))
    searched_in = _files_line(targets)
    if not hits:
        line = (
            f"No hit for «{phrase}» in the full text of {searched_in}. Try another wording, a "
            "shorter phrase or one key word, or read the sections the outline points to."
        )
        return line + (f" Unknown file(s): {', '.join(unknown)}." if unknown else "")

    total = len(hits)
    if total > FIND_MAX_HITS:
        keep = sorted(sorted(range(total), key=lambda i: (not hits[i][3], i))[:FIND_MAX_HITS])
        hits = [hits[i] for i in keep]

    # Hits whose passages overlap share one passage.
    passages: list[list[Any]] = []
    for f, s, e, _whole in hits:
        a, b = _window(f.view.text, s, e)
        last = passages[-1] if passages else None
        if last is not None and last[0] is f and a <= last[2]:
            last[2] = max(last[2], b)
            last[3] += 1
        else:
            passages.append([f, a, b, 1, s])

    head = f"find «{phrase}» in {searched_in}: {total} hit{'s' if total != 1 else ''}"
    if total > FIND_MAX_HITS:
        head += f", showing {FIND_MAX_HITS} — search a longer phrase to narrow it"
    lines = [head + "."]
    for f, a, b, count, first in passages:
        text = f.view.text[a:b]
        f.mark_seen(a, b)
        deps.chars_read += len(text)
        label = where(f, first) + (f" · {count} hits" if count > 1 else "")
        lines.append(f"\n[{label}]\n{'…' if a > 0 else ''}{text.strip()}{'…' if b < len(f.view.text) else ''}")
    if unknown:
        lines.append(f"\nUnknown file(s): {', '.join(unknown)}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# read_sections
# ---------------------------------------------------------------------------


def parse_refs(deps: AttachmentDeps, refs: list[str]) -> tuple[list[tuple[FileDoc, SectionRow]], list[str]]:
    """``F1·S7`` / ``F1.S7`` / ``WI-3·S7`` / ``F1·S7–S9`` / bare ``S7`` (one file with
    sections) → sections, in the order asked; plus what did not resolve."""
    with_sections = [f for f in deps.files.values() if f.sections]
    wanted: list[tuple[FileDoc, SectionRow]] = []
    bad: list[str] = []
    for raw in refs or []:
        m = _REF_RE.match(str(raw or ""))
        if not m:
            bad.append(str(raw))
            continue
        if m.group("f"):
            f = deps.file(f"F{m.group('f')}")
        elif m.group("wi"):
            f = deps.file(f"WI-{m.group('wi')}")
        else:
            f = with_sections[0] if len(with_sections) == 1 else None
        if f is None:
            bad.append(str(raw))
            continue
        first = int(m.group("s"))
        last = int(m.group("to")) if m.group("to") else first
        if last < first:
            first, last = last, first
        seqs = range(first, min(last, first + MAX_RANGE_SECTIONS - 1) + 1)
        hit = False
        for seq in seqs:
            section = f.section(seq)
            if section is not None:
                hit = True
                if (f, section) not in wanted:
                    wanted.append((f, section))
        if not hit:
            bad.append(str(raw))
    return wanted, bad


def read_sections_text(deps: AttachmentDeps, refs: list[str]) -> str:
    wanted, bad = parse_refs(deps, refs)
    budget = READ_MAX_CHARS
    blocks: list[str] = []
    not_read: list[str] = []
    for f, s in wanted:
        if budget <= 0:
            not_read.append(f.ref(s))
            continue
        start, end = f.view.to_masked(s.char_start), f.view.to_masked(s.char_end)
        text = s.joined_text or f.view.text[start:end]
        cut = len(text) > budget
        if cut:
            text = text[:budget]
            end = min(end, start + budget) if not s.joined_text else min(end, start + int(budget * 1.1))
        f.mark_seen(start, end)
        deps.chars_read += len(text)
        budget -= len(text)
        header = f"### {section_line(f, s)}"
        if s.joined_text:
            header += " · (page letterheads inside it left out)"
        block = f"{header}\n{text.strip()}"
        if cut:
            pages = f" (pages {s.page_from}–{s.page_to})" if s.page_from is not None else ""
            block += f"\n[cut here: the section goes on{pages} — read the rest with read_pages]"
        blocks.append(block)
    if not_read:
        blocks.append(
            f"Not read (over this call's {READ_MAX_CHARS:,}-character limit): {', '.join(not_read)} — "
            "ask for them in another call."
        )
    if bad:
        no_map = [f.label for f in deps.files.values() if not f.sections]
        hint = f" {', '.join(no_map)} ha{'s' if len(no_map) == 1 else 've'} no sections: read with find / read_pages." if no_map else ""
        blocks.append(f"Unknown section refs: {', '.join(bad)}. Use refs from the outline, e.g. F1·S7.{hint}")
    return "\n\n".join(blocks) or "No section refs given."


# ---------------------------------------------------------------------------
# read_pages
# ---------------------------------------------------------------------------


def read_pages_text(deps: AttachmentDeps, file: str, from_page: int, to_page: int | None = None) -> str:
    f = deps.file(file)
    if f is None:
        return f"Unknown file {file!r}. Files: {_files_line(list(deps.files.values()))}."
    first = int(from_page or 1)
    last = int(to_page) if to_page is not None else first
    if last < first:
        first, last = last, first

    if f.pages:
        numbers = [no for no, _start in f.pages]
        unit, total = "page", numbers[-1]
        ranges = [(no, f.page_range(no)) for no in numbers if first <= no <= last]
        if not ranges:
            return f"{f.label} has pages {numbers[0]}–{numbers[-1]}."
    else:
        unit, total = "part", len(f.parts)
        if not f.parts:
            return f"{f.label} has no text."
        ranges = [(n, f.parts[n - 1]) for n in range(max(first, 1), min(last, total) + 1)]
        if not ranges:
            return f"{f.label} has no page numbers; it is read in parts 1–{total}."

    budget = READ_MAX_CHARS
    blocks: list[str] = []
    stopped_at: int | None = None
    for no, rng in ranges:
        if rng is None:
            continue
        start, end = rng
        if budget <= 0:
            stopped_at = no
            break
        if end - start > budget:
            end = start + budget
        text = f.view.text[start:end]
        f.mark_seen(start, end)
        deps.chars_read += len(text)
        budget -= len(text)
        tail = " (this file has no page numbers)" if unit == "part" else ""
        cut = "\n[cut here: this page goes on — the call's limit was reached]" if end < rng[1] else ""
        blocks.append(f"## {f.label} — {unit} {no} of {total}{tail}\n{text.strip()}{cut}")
    if stopped_at is not None:
        blocks.append(
            f"Stopped at {unit} {stopped_at} (this call's {READ_MAX_CHARS:,}-character limit) — "
            f"call read_pages again from {unit} {stopped_at}."
        )
    return "\n\n".join(blocks) or f"{f.label}: nothing in {unit}s {first}–{last}."


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_tools(agent: Agent[AttachmentDeps, Any]) -> None:
    @agent.tool
    async def find(ctx: RunContext[AttachmentDeps], phrase: str, files: list[str] | None = None) -> str:
        """Search the FULL text of the files for a word or phrase.

        Tashkeel, alef / hamza forms, ة/ه and ى/ي are ignored, and Arabic-Indic and
        Latin digits match each other. Returns every hit (up to 20) with its file,
        section, page and about 300 characters around it.

        Args:
            phrase: A short, distinctive word or phrase, as it would be written in the file.
            files: File labels to search, e.g. ["F1"]. Leave empty to search every file.
        """
        return find_text(ctx.deps, phrase, files)

    @agent.tool
    async def read_sections(ctx: RunContext[AttachmentDeps], refs: list[str]) -> str:
        """Read whole sections from the outline. Ask for all you need in one call.

        Args:
            refs: Section refs as the outline writes them, e.g. ["F1·S7", "F1·S9"];
                a run as "F1·S7–S9".
        """
        return read_sections_text(ctx.deps, refs)

    @agent.tool
    async def read_pages(
        ctx: RunContext[AttachmentDeps], file: str, from_page: int, to_page: int | None = None,
    ) -> str:
        """Read pages of one file. A file with no page numbers is read in numbered
        parts instead (the header says how many): pass part numbers the same way.

        Args:
            file: The file label, e.g. "F1".
            from_page: First page (or part), 1-based.
            to_page: Last page (or part), inclusive. Empty = only from_page.
        """
        return read_pages_text(ctx.deps, file, from_page, to_page)


__all__ = [
    "FIND_MAX_HITS",
    "MAX_RANGE_SECTIONS",
    "PASSAGE_CHARS",
    "READ_MAX_CHARS",
    "find_text",
    "parse_refs",
    "read_pages_text",
    "read_sections_text",
    "register_tools",
    "where",
]
