"""Join a core section that a page letterhead cut in two (owner, 2026-11-18).

The model closes a section at every repeated page letterhead, so one passage that
runs over a page break comes back as ``core`` · ``page_letterhead`` · ``core``
(e.g. «الدفع الثامن» on page 3, the letterhead, «استكمال الدفع الثامن» on page 4).
Code puts the two halves back together, and nothing else is merged.

Two ``core`` sections are joined when ALL of these hold:

- only ``page_letterhead`` sections lie between them (any value: ``boilerplate`` or
  a code-marked ``repeat``);
- they have the same ``label`` and the same owner (``owner_class`` + ``owner``);
- they sit in the same document of the file (``documents_in_file``);
- the joined text stays within :data:`PAGE_JOIN_MAX_CHARS` — a long table that runs
  over thirty pages is not chained into one huge section.

The joined section spans the whole range (``from_p``..``to_p``, ``char_start``..
``char_end``, pages), but its text is the core parts joined by a newline: the
letterhead between them is dropped, so the passage reads straight through. The
letterhead rows themselves disappear (their paragraphs now sit inside the joined
range). Its ``what`` keeps every part's description, joined by «؛».

Second step — :func:`merge_short_cores` (owner, 2026-11-18), run AFTER the page
joins: no ``core`` section stays under :data:`SHORT_CORE_MIN_CHARS`. A short one
(a heading such as «ثالثاً: الحضور / ١. عن شركة …:» the model closed on its own)
merges into the section right AFTER it when that one is ``core``; otherwise into
the section right BEFORE it when that one is ``core``; when neither is ``core`` it
stays as it is. The descriptions are joined in document order.

Pure: rows in, rows out (the dicts ``runner._segment_rows`` builds).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

#: The label the prompt gives repeated letterheads, footers and page numbers.
PAGE_LETTERHEAD_LABEL = "page_letterhead"
#: A joined section's text never passes this (owner, 2026-11-18).
PAGE_JOIN_MAX_CHARS = 5_000
#: No core section is shorter than this after the second step (owner, 2026-11-18).
SHORT_CORE_MIN_CHARS = 100
WHAT_SEPARATOR = "؛ "


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split())


def _doc_index(docs: Sequence[Mapping[str, Any]], p: int) -> int:
    for i, d in enumerate(docs):
        try:
            if int(d.get("from_p", 0)) <= p <= int(d.get("to_p", 0)):
                return i
        except (TypeError, ValueError):
            continue
    return -1


def _same_owner(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (_norm(a.get("owner_class")) == _norm(b.get("owner_class"))
            and _norm(a.get("owner")) == _norm(b.get("owner")))


def _join_what(a: str, b: str) -> str:
    parts = [x for x in (_norm(p) for p in f"{a}{WHAT_SEPARATOR}{b}".split(WHAT_SEPARATOR.strip())) if x]
    return WHAT_SEPARATOR.join(dict.fromkeys(parts))


def join_page_breaks(
    rows: Sequence[Mapping[str, Any]],
    documents_in_file: Sequence[Mapping[str, Any]] = (),
    *,
    max_chars: int = PAGE_JOIN_MAX_CHARS,
) -> tuple[list[dict], int]:
    """``(rows, joins)`` — the rows with every page-cut core section joined back,
    ``seq`` renumbered from 1. ``joins`` counts the joins made (0 = rows unchanged)."""
    src = sorted((dict(r) for r in rows), key=lambda r: int(r.get("seq") or 0))
    out: list[dict] = []
    joins = 0
    i = 0
    while i < len(src):
        cur = src[i]
        i += 1
        if cur.get("value") != "core":
            out.append(cur)
            continue
        while True:
            k = i
            while k < len(src) and src[k].get("label") == PAGE_LETTERHEAD_LABEL:
                k += 1
            if k == i or k >= len(src):
                break
            nxt = src[k]
            text_a, text_b = str(cur.get("content_md") or ""), str(nxt.get("content_md") or "")
            if (
                nxt.get("value") != "core"
                or nxt.get("label") != cur.get("label")
                or not _same_owner(cur, nxt)
                or _doc_index(documents_in_file, int(nxt["from_p"])) != _doc_index(documents_in_file, int(cur["from_p"]))
                or len(text_a) + 1 + len(text_b) > max_chars
            ):
                break
            joined = {
                **cur,
                "to_p": nxt["to_p"],
                "page_to": nxt.get("page_to"),
                "char_end": nxt["char_end"],
                "what": _join_what(str(cur.get("what") or ""), str(nxt.get("what") or "")),
                "content_md": f"{text_a.rstrip()}\n{text_b.lstrip()}",
            }
            if "brought_by" in cur or "brought_by" in nxt:  # pre-v5.2 rows have no owner keys
                joined["brought_by"] = cur.get("brought_by") or nxt.get("brought_by")
            cur = joined
            joins += 1
            i = k + 1
        out.append(cur)
    for seq, r in enumerate(out, start=1):
        r["seq"] = seq
    return out, joins


def _is_short_core(r: Mapping[str, Any], min_chars: int) -> bool:
    return r.get("value") == "core" and len(str(r.get("content_md") or "").strip()) < min_chars


def _merge_neighbours(first: Mapping[str, Any], second: Mapping[str, Any], *, into: Mapping[str, Any]) -> dict:
    """Two neighbouring rows (``first`` before ``second``) as one. ``into`` — the
    one the short section merges INTO — keeps its label, value and owner."""
    merged = {
        **into,
        "from_p": first["from_p"],
        "page_from": first.get("page_from"),
        "char_start": first["char_start"],
        "to_p": second["to_p"],
        "page_to": second.get("page_to"),
        "char_end": second["char_end"],
        "what": _join_what(str(first.get("what") or ""), str(second.get("what") or "")),
        "content_md": f'{str(first.get("content_md") or "").rstrip()}\n{str(second.get("content_md") or "").lstrip()}',
    }
    if "brought_by" in first or "brought_by" in second:  # pre-v5.2 rows have no owner keys
        other = second if into is first else first
        merged["brought_by"] = into.get("brought_by") or other.get("brought_by")
    return merged


def merge_short_cores(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_chars: int = SHORT_CORE_MIN_CHARS,
) -> tuple[list[dict], int]:
    """``(rows, merges)`` — every ``core`` section under ``min_chars`` merged into the
    next section when it is ``core``, else into the previous one when it is ``core``,
    else left alone; ``seq`` renumbered from 1. A section still short after merging
    forward is checked again, so a run of short ones folds into the first longer
    core after them."""
    src = sorted((dict(r) for r in rows), key=lambda r: int(r.get("seq") or 0))
    out: list[dict] = []
    merges = 0
    for i, cur in enumerate(src):
        if _is_short_core(cur, min_chars):
            if i + 1 < len(src) and src[i + 1].get("value") == "core":
                src[i + 1] = _merge_neighbours(cur, src[i + 1], into=src[i + 1])
                merges += 1
                continue
            if out and out[-1].get("value") == "core":
                out[-1] = _merge_neighbours(out[-1], cur, into=out[-1])
                merges += 1
                continue
        out.append(cur)
    for seq, r in enumerate(out, start=1):
        r["seq"] = seq
    return out, merges


__all__ = [
    "PAGE_JOIN_MAX_CHARS",
    "PAGE_LETTERHEAD_LABEL",
    "SHORT_CORE_MIN_CHARS",
    "join_page_breaks",
    "merge_short_cores",
]
