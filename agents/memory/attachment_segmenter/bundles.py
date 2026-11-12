"""Bundles — the small attachments of one message, segmented as ONE run.

Owner rules (2026-11-12), applied per user message (``message_attachments``, the
files the user sent together):

1. every attachment over ``SEGMENT_MIN_CHARS`` (5,000) gets its OWN run — one call,
   or split in two above ~30k;
2. the message's remaining attachments (each ≤ 5,000): when there are 2+ of them and
   their total is over 5,000 → ONE bundle run (one call, or split in two when the
   bundle's combined size is above ~30k). Otherwise they are not segmented (they
   travel whole).

An attachment with no message link only follows rule 1. Example: 40k, 20k, 4k, 2k,
4k sent together → [40k] (split → 2 calls), [20k], [4k + 2k + 4k].

This module is the planning half: the pure planner, the DB lookups that find an
item's bundle, the global numbering of a bundle's paragraphs and the cut back to
per-file maps. ``runner.run_attachment_bundle`` does the run.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from agents.utils.attachment_delivery import SEGMENT_MIN_CHARS

from .models import EmbeddedDoc, Segment, SegmentMap
from .paragraphs import Paragraph

logger = logging.getLogger(__name__)

#: uuid5 namespace of ``attachment_segment_maps.bundle_id`` (fixed forever: the id
#: of a message's bundle must not change between runs).
BUNDLE_NAMESPACE = uuid.UUID("5b0f5c1e-2d1f-4f43-9a52-8f1b6a0c2d13")

#: Mixed doc_types (no single most common one) → the generic label list.
MIXED_DOC_TYPE = "mixed"

_MEMBER_COLUMNS = "item_id, user_id, kind, content_md, wi_seq, created_at, deleted_at"


# ---------------------------------------------------------------------------
# The planner (pure)
# ---------------------------------------------------------------------------


def plan_units(items: Iterable[Mapping[str, Any]], *, min_chars: int = SEGMENT_MIN_CHARS) -> list[list[str]]:
    """The segmentation units of a set of attachments, each a list of item ids.

    ``items``: dicts with ``item_id``, ``chars`` (``len(content_md)``),
    ``message_id`` (the message the file was sent with; empty / ``None`` = no link)
    and ``order`` (upload order — e.g. ``(wi_seq, created_at)``; items without one
    keep their input order, after the others).

    Out: one solo unit per item over ``min_chars`` and at most one bundle per message
    (2+ items of that message each ≤ ``min_chars``, together over ``min_chars``),
    members in upload order. Units come in upload order (a bundle sits where its
    first member was uploaded). Items with no text (``chars <= 0``) are never units.
    """
    rows = []
    for idx, it in enumerate(items):
        item_id = str(it.get("item_id") or "")
        chars = int(it.get("chars") or 0)
        if not item_id or chars <= 0:
            continue
        order = it.get("order")
        key = (0, order, idx) if order is not None else (1, 0, idx)
        rows.append((key, item_id, chars, str(it.get("message_id") or "")))
    rows.sort(key=lambda r: r[0])

    units: list[list[str] | str] = []      # a str entry = a message's bundle slot
    small: dict[str, list[tuple[str, int]]] = {}
    for _key, item_id, chars, msg in rows:
        if chars > min_chars:
            units.append([item_id])
        elif msg:
            if msg not in small:
                small[msg] = []
                units.append(msg)
            small[msg].append((item_id, chars))

    out: list[list[str]] = []
    for u in units:
        if isinstance(u, list):
            out.append(u)
            continue
        members = small[u]
        if len(members) >= 2 and sum(c for _, c in members) > min_chars:
            out.append([i for i, _ in members])
    return out


def bundle_id_for(message_id: str | None, member_ids: Sequence[str] = ()) -> str:
    """The id shared by a bundle's maps: uuid5 of the message id (stable while the
    message's files change), or of the sorted member ids without one."""
    if message_id:
        return str(uuid.uuid5(BUNDLE_NAMESPACE, f"message:{message_id}"))
    return str(uuid.uuid5(BUNDLE_NAMESPACE, "items:" + ",".join(sorted(map(str, member_ids)))))


def bundle_doc_type(doc_types: Sequence[str | None]) -> str:
    """The doc_type whose label list the bundle prompt uses: the most common one
    among the files; a tie for most common → ``mixed``; most common unset → ``""``.
    Both of those get the generic list (``prompts.type_key``)."""
    counts = Counter(str(d or "") for d in doc_types)
    if not counts:
        return ""
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return MIXED_DOC_TYPE
    return top[0][0]


# ---------------------------------------------------------------------------
# DB lookups (sync — the frame / runner call them in a thread)
# ---------------------------------------------------------------------------


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def primary_messages(supabase: Any, item_ids: Sequence[str]) -> dict[str, str]:
    """``{item_id: message_id}`` — the FIRST message each item was sent with (an item
    re-sent with a later message still belongs to its first one; unique per pair)."""
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    rows = _data(
        supabase.table("message_attachments")
        .select("message_id, document_id, created_at")
        .in_("document_id", ids)
        .execute()
    )
    best: dict[str, tuple[str, str]] = {}
    for r in rows:
        doc, msg = str(r.get("document_id") or ""), str(r.get("message_id") or "")
        if not doc or not msg:
            continue
        key = (str(r.get("created_at") or ""), msg)
        if doc not in best or key < best[doc]:
            best[doc] = key
    return {doc: key[1] for doc, key in best.items()}


def upload_order(row: Mapping[str, Any]) -> tuple:
    """Upload order of a workspace_items row: ``wi_seq``, then ``created_at``."""
    seq = row.get("wi_seq")
    return (seq if isinstance(seq, int) else 1 << 30, str(row.get("created_at") or ""))


def _live_attachment(row: Mapping[str, Any] | None) -> bool:
    return bool(row) and str(row.get("kind") or "") == "attachment" and not row.get("deleted_at")


def message_members(supabase: Any, message_id: str, user_id: str | None = None) -> list[dict]:
    """The live attachments whose FIRST message is ``message_id``, with ``chars`` and
    ``order``, in upload order."""
    links = _data(
        supabase.table("message_attachments")
        .select("document_id")
        .eq("message_id", message_id)
        .execute()
    )
    doc_ids = [str(r["document_id"]) for r in links if r.get("document_id")]
    if not doc_ids:
        return []
    primary = primary_messages(supabase, doc_ids)
    doc_ids = [d for d in doc_ids if primary.get(d) == message_id]
    q = supabase.table("workspace_items").select(_MEMBER_COLUMNS).in_("item_id", doc_ids)
    if user_id:
        q = q.eq("user_id", user_id)
    rows = [r for r in _data(q.execute()) if _live_attachment(r)]
    rows.sort(key=upload_order)
    return [
        {
            "item_id": str(r["item_id"]),
            "chars": len(r.get("content_md") or ""),
            "message_id": message_id,
            "order": upload_order(r),
        }
        for r in rows
    ]


def _item_row(supabase: Any, item_id: str) -> dict | None:
    rows = _data(
        supabase.table("workspace_items")
        .select(_MEMBER_COLUMNS)
        .eq("item_id", item_id)
        .limit(1)
        .execute()
    )
    return rows[0] if rows else None


def bundle_for_item(supabase: Any, item_id: str, *, min_chars: int = SEGMENT_MIN_CHARS) -> list[str] | None:
    """The qualifying bundle that contains ``item_id`` — its member ids in upload
    order — or ``None`` (a big file, a file without a message link, a message whose
    small files do not qualify, a failed read). Never raises."""
    try:
        row = _item_row(supabase, str(item_id))
        if not _live_attachment(row) or len(row.get("content_md") or "") > min_chars:
            return None
        msg = primary_messages(supabase, [str(item_id)]).get(str(item_id))
        if not msg:
            return None
        members = message_members(supabase, msg, str(row.get("user_id") or "") or None)
        for unit in plan_units(members, min_chars=min_chars):
            if str(item_id) in unit and len(unit) >= 2:
                return unit
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_segmenter: bundle lookup failed for %s (%s)", item_id, type(exc).__name__)
        return None


def segmenter_applies_to_item(supabase: Any, item_id: str, *, min_chars: int = SEGMENT_MIN_CHARS) -> bool:
    """For the helper frame's ``applies_to``: an attachment over ``min_chars``, or a
    member of a qualifying bundle. A failed read = ``False``. Never raises."""
    try:
        row = _item_row(supabase, str(item_id))
        if not _live_attachment(row):
            return False
        if len(row.get("content_md") or "") > min_chars:
            return True
        return bundle_for_item(supabase, str(item_id), min_chars=min_chars) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_segmenter: applies_to failed for %s (%s)", item_id, type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Global numbering, boundary split, cut back (pure)
# ---------------------------------------------------------------------------

#: Synthetic page key per file: file i's pages become ``i * _PAGE_STRIDE + page``, so a
#: file start always reads as a page start (a natural split cut) and pages never
#: collide across files.
_PAGE_STRIDE = 100_000
#: Virtual gap between files in the global char axis (only ``choose_cut`` reads it).
_FILE_GAP = 2


@dataclass(frozen=True)
class FileSpan:
    """Where one member's paragraphs sit in the bundle's global numbering."""

    item_id: str
    first_p: int
    last_p: int

    def __contains__(self, p: int) -> bool:
        return self.first_p <= p <= self.last_p

    def local(self, p: int) -> int:
        return p - self.first_p + 1


def number_globally(
    item_ids: Sequence[str],
    paragraphs_by_file: Sequence[Sequence[Paragraph]],
    contents: Sequence[str],
) -> tuple[list[Paragraph], list[FileSpan]]:
    """One global paragraph list (P001… across every file, in the given order) and
    each file's span. Global paragraphs keep their text; their char offsets and
    pages are synthetic (monotonic across files) — never slice with them."""
    out: list[Paragraph] = []
    spans: list[FileSpan] = []
    base = 0
    for idx, (item_id, paras, content) in enumerate(zip(item_ids, paragraphs_by_file, contents), start=1):
        first = len(out) + 1
        for p in paras:
            out.append(Paragraph(
                n=len(out) + 1,
                char_start=base + p.char_start,
                char_end=base + p.char_end,
                text=p.text,
                page=idx * _PAGE_STRIDE + (p.page or 0),
            ))
        spans.append(FileSpan(str(item_id), first, len(out)))
        base += len(content) + _FILE_GAP
    return out, spans


def split_at_boundaries(segments: Sequence[Segment], spans: Sequence[FileSpan]) -> tuple[list[Segment], int]:
    """Every segment that runs across a file boundary becomes one piece per file
    (same label, value and description). Returns the segments and how many cuts."""
    starts = [s.first_p for s in spans[1:] if s.last_p >= s.first_p]
    out: list[Segment] = []
    cuts = 0
    for seg in segments:
        inside = [b for b in starts if seg.from_p < b <= seg.to_p]
        a = seg.from_p
        for b in inside:
            out.append(seg.model_copy(update={"from_p": a, "to_p": b - 1}))
            a = b
            cuts += 1
        out.append(seg.model_copy(update={"from_p": a, "to_p": seg.to_p}))
    return out, cuts


def cut_back(smap: SegmentMap, span: FileSpan) -> SegmentMap:
    """One file's own map out of the bundle's map (already split at boundaries):
    its segments, documents and key paragraphs, renumbered from 1."""
    segs = [
        s.model_copy(update={"from_p": span.local(s.from_p), "to_p": span.local(s.to_p)})
        for s in smap.segments
        if s.from_p in span and s.to_p in span
    ]
    docs: list[EmbeddedDoc] = []
    for d in smap.documents_in_file:
        lo, hi = max(d.from_p, span.first_p), min(d.to_p, span.last_p)
        if lo <= hi:
            docs.append(d.model_copy(update={"from_p": span.local(lo), "to_p": span.local(hi)}))
    keys = [k.model_copy(update={"p": span.local(k.p)}) for k in smap.key_paragraphs if k.p in span]
    return SegmentMap(documents_in_file=docs, segments=segs, key_paragraphs=keys)


def split_evenly(total: int, weights: Sequence[int]) -> list[int]:
    """``total`` shared by ``weights`` (integer shares that add up to ``total``)."""
    if not weights:
        return []
    w = [max(0, int(x)) for x in weights]
    s = sum(w) or len(w)
    w = w if sum(w) else [1] * len(w)
    shares = [total * x // s for x in w]
    shares[0] += total - sum(shares)
    return shares


__all__ = [
    "BUNDLE_NAMESPACE",
    "FileSpan",
    "MIXED_DOC_TYPE",
    "bundle_doc_type",
    "bundle_for_item",
    "bundle_id_for",
    "cut_back",
    "message_members",
    "number_globally",
    "plan_units",
    "primary_messages",
    "segmenter_applies_to_item",
    "split_at_boundaries",
    "split_evenly",
    "upload_order",
]
