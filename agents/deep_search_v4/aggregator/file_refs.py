"""Resolve the file tags the aggregator wrote (aggregator_file_refs.md §4.4, D11–D12).

The aggregator's output format is unchanged (owner decision D5, option A): it only
writes ``[F1·S7]`` / ``[F1]`` in ``synthesis_md`` — F + the file's fixed WI number in
the conversation (its ``### WI-1 · …`` heading) and the section's number. After the
run, code reads the tags (``shared/workspace/file_refs.py`` — the one grammar) and
keeps each one that points at something the aggregator was actually shown
(``FileRefDoc``, keyed by WI number, built while its case_document list was
numbered):

* ``[F·S]`` — a document shown as sections, and a section it was shown;
* ``[F]``   — a document shown as whole text (no sections);
* anything else (a WI number not shown with text — a summary-only document, a
  report, an unknown number —, an unknown section, ``[F]`` on a sectioned
  document, ``[F·S]`` on a whole one) is not resolved: no row, so the web hides
  the tag.

Code never edits, moves or removes a tag (D3). Pure; never raises.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from shared.workspace.file_refs import file_tags, split_file_ref

from .models import FileRef

logger = logging.getLogger(__name__)


def _position(value: int) -> int | None:
    return int(value) if value is not None and int(value) >= 0 else None


def build_file_refs(synthesis_md: str | None, docs: Iterable[Any] | None) -> list[FileRef]:
    """One :class:`FileRef` per distinct resolvable tag, in order of first mention
    (``n`` 1..k). ``docs`` — the turn's ``FileRefDoc`` list."""
    try:
        by_wi = {int(d.wi_seq): d for d in docs or []}
        if not by_wi:
            return []
        out: list[FileRef] = []
        for ref in file_tags(synthesis_md):
            parsed = split_file_ref(ref)
            if parsed is None:
                continue
            wi, seq = parsed
            doc = by_wi.get(wi)
            if doc is None:
                continue
            sections: Sequence[tuple[int, int, int]] = tuple(doc.sections or ())
            if seq is None:
                if sections:
                    continue  # a sectioned document is cited by its sections
                out.append(FileRef(
                    n=len(out) + 1, ref_id=ref, item_id=str(doc.item_id), wi_seq=wi,
                ))
                continue
            hit = next((s for s in sections if int(s[0]) == seq), None)
            if hit is None:
                continue
            out.append(FileRef(
                n=len(out) + 1,
                ref_id=ref,
                item_id=str(doc.item_id),
                wi_seq=wi,
                section_seq=seq,
                char_start=_position(hit[1]),
                char_end=_position(hit[2]),
            ))
        return out
    except Exception:  # noqa: BLE001 — file refs are additive; the answer ships regardless
        logger.warning("aggregator: file refs not built", exc_info=True)
        return []


__all__ = ["build_file_refs"]
