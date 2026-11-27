"""Citation safety by code — the merger's shared reference list (plan §4.6).

1. Every report's references merge into ONE list, deduplicated by ``ref_id``
   (else by type + source + title + article), and every report's ``[n]`` is
   rewritten to that shared numbering BEFORE the model sees it.
2. The model may cite only numbers of that list (the agent's validator).
3. The answer is renumbered 1..k in order of first citation and only the cited
   references are kept — the same contract as the aggregator's output.

Ported from the tested experiment mergers (11-15 ``planner_threads/merger.py``,
11-21 ``run_angle_merge.py``).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agents.deep_search_v4.aggregator.models import Reference

if TYPE_CHECKING:
    from .models import MergerReport

#: ``[3]``, ``[3,7]``, ``[3، 7]`` — Western digits (Arabic-Indic ones are
#: normalised first by :func:`western_citations`).
CITE_RE = re.compile(r"\[(\d+(?:\s*[,،]\s*\d+)*)\]")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_INDIC_TAG = re.compile(r"\[([0-9٠-٩۰-۹](?:[0-9٠-٩۰-۹\s,،])*)\]")


def ref_key(ref: Reference) -> tuple:
    if (ref.ref_id or "").strip():
        return ("id", ref.ref_id.strip())
    return ("t", ref.source_type, ref.regulation_title or "", ref.title or "", ref.article_num or "")


def western_citations(text: str) -> str:
    """``[١١]`` → ``[11]`` inside citation tags only (an Arabic-Indic digit breaks the link)."""
    return _INDIC_TAG.sub(lambda m: "[" + m.group(1).translate(_ARABIC_INDIC) + "]", text or "")


def cited_numbers(text: str) -> list[int]:
    """Distinct cited numbers in order of first use."""
    seen: list[int] = []
    for m in CITE_RE.finditer(text or ""):
        for x in re.split(r"\s*[,،]\s*", m.group(1)):
            n = int(x)
            if n not in seen:
                seen.append(n)
    return seen


def remap_citations(text: str, mapping: dict[int, int]) -> tuple[str, int]:
    """Rewrite every ``[n,m]`` through ``mapping``; numbers with no mapping are
    dropped (a tag left empty is removed). Returns the text and how many numbers
    were dropped."""
    dropped = 0

    def sub(m: re.Match) -> str:
        nonlocal dropped
        new: list[int] = []
        for x in (int(v) for v in re.split(r"\s*[,،]\s*", m.group(1))):
            if x in mapping:
                if mapping[x] not in new:
                    new.append(mapping[x])
            else:
                dropped += 1
        return f"[{','.join(map(str, new))}]" if new else ""

    return CITE_RE.sub(sub, text or ""), dropped


def strip_citations(text: str) -> str:
    """Remove ``[n]`` tags from text code does not renumber (scoreboard notes, decisions, gaps)."""
    return re.sub(r"\s{2,}", " ", CITE_RE.sub("", western_citations(text or ""))).strip()


def build_shared_references(
    reports: Sequence["MergerReport"],
) -> tuple[list[Reference], list[str | None], dict[int, list[int]], int]:
    """One deduplicated reference list over the reports that finished.

    Returns ``(shared, bodies, sources, dropped)``:
    - ``shared[i]`` = shared reference ``i + 1`` (its own ``.n`` is still the
      report-local number — the prompt numbers by position);
    - ``bodies[j]`` = report ``j``'s ``synthesis_md`` renumbered to ``shared``
      (``None`` for a failed report);
    - ``sources`` = shared n → the report numbers (1-based) whose body cites it;
    - ``dropped`` = report tags that pointed at no reference of their report.
    """
    shared: list[Reference] = []
    index: dict[tuple, int] = {}
    bodies: list[str | None] = []
    sources: dict[int, list[int]] = {}
    dropped = 0
    for j, report in enumerate(reports, 1):
        out = report.output
        if out is None:
            bodies.append(None)
            continue
        mapping: dict[int, int] = {}
        for ref in out.references or []:
            k = ref_key(ref)
            if k not in index:
                shared.append(ref)
                index[k] = len(shared)
            mapping[int(ref.n)] = index[k]
        body, d = remap_citations(western_citations(out.synthesis_md or ""), mapping)
        dropped += d
        bodies.append(body)
        for n in cited_numbers(body):
            sources.setdefault(n, [])
            if j not in sources[n]:
                sources[n].append(j)
    return shared, bodies, sources, dropped


def renumber_answer(
    text: str, shared: Sequence[Reference]
) -> tuple[str, list[Reference], dict[int, int]]:
    """Renumber the answer's citations 1..k in order of first use; keep only the
    cited references (copies with their new ``n``). Numbers outside the list
    are dropped. Returns ``(text, references, old→new)``."""
    order = [n for n in cited_numbers(text) if 1 <= n <= len(shared)]
    final = {old: new for new, old in enumerate(order, 1)}
    body, _ = remap_citations(text, final)
    refs = [shared[old - 1].model_copy(update={"n": final[old]}) for old in order]
    return body, refs, final


__all__ = [
    "CITE_RE",
    "build_shared_references",
    "cited_numbers",
    "ref_key",
    "remap_citations",
    "renumber_answer",
    "strip_citations",
    "western_citations",
]
