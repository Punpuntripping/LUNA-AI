"""Attachment segmenter — Layer 4 Memory, model tier_2 (slot ``attachment_segmenter``).

Maps an OCR'd attachment over 5,000 characters into labelled sections rated
``metadata`` / ``core`` / ``supporting`` / ``boilerplate`` (code adds ``repeat``),
WITHOUT rewriting a word: code cuts the original ``content_md`` at paragraph
boundaries and stores pages + sections beside it (migration 215). Delivery code
reads them with :func:`load_segment_maps`.

Plan: ``.claude/plans/attachment_segmentation.md`` §4.3–§4.5. Gate:
``ATTACHMENT_SEGMENTER_ENABLED`` (+ ``ATTACHMENT_SEGMENTER_USERS``), see
``agents/utils/segmenter_flags.py``. Not wired into the orchestrator: the helper
frame (``.claude/plans/helper_frame.md``) starts it.

Bundles (owner, 2026-11-12): the small files of one message (each <= 5,000 chars,
2+ of them, together > 5,000) are segmented in ONE run — ``bundles.py``,
``run_attachment_bundle``; ``run_attachment_segmenter`` joins it for a member.
"""
from __future__ import annotations

from .bundles import bundle_for_item, plan_units, segmenter_applies_to_item
from .models import SegmenterResult, SegmentMap, StoredMap, StoredSegment
from .paragraphs import PARAGRAPH_RULE_VERSION, Paragraph, paragraphize
from .prompts import PROMPT_VERSION
from .runner import SEGMENT_MIN_CHARS, run_attachment_bundle, run_attachment_segmenter, segmenter_applies
from .store import load_segment_map, load_segment_maps

__all__ = [
    "PARAGRAPH_RULE_VERSION",
    "PROMPT_VERSION",
    "Paragraph",
    "SEGMENT_MIN_CHARS",
    "SegmentMap",
    "SegmenterResult",
    "StoredMap",
    "StoredSegment",
    "bundle_for_item",
    "load_segment_map",
    "load_segment_maps",
    "paragraphize",
    "plan_units",
    "run_attachment_bundle",
    "run_attachment_segmenter",
    "segmenter_applies",
    "segmenter_applies_to_item",
]
