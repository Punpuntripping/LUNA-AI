"""Angle mapper (خريطة الأوجه) — Layer 4 Memory, model tier_2 (slot ``angle_mapper``).

Reads the attachments a user sent in ONE message — each segmented attachment's
sections with their owners, the user's own words, each summary, every document's
whole text as numbered paragraphs — and names their ANGLES in one call: the lines
of argument worth a deep search, ``strengthen`` (builds the client's position),
``attack`` (undermines the opponent's), ``answer`` (an opponent's strong point the
client must answer). Each angle points at sections by document-qualified refs
(``"D1.S7"``, :mod:`.refs`), so an angle can connect sections of different
documents. Code labels them الموكّل / الخصم / محايد from their owners
(:mod:`.labels`), never stored. A message with one file is a group of one.

Plan: ``.claude/plans/relationship_map.md`` §4, D16–D17 (prompt angles-v5). Gate:
``ANGLE_MAP_ENABLED`` (+ ``ANGLE_MAP_USERS``, and the segmenter on for the user),
see ``agents/utils/angle_map_flags.py``. Started by the helper frame per item after
the segmenter (``agents/helpers/registry.py`` ``ANGLE_MAP``); the runner makes the
group run once (``runner.py``); critical for no consumer.
"""
from __future__ import annotations

from .labels import LABEL_AR, angle_viewpoint, relative_label, resolve_ref, section_index, section_side
from .models import Angle, AngleMap, AngleMapRunResult, Client, ClientInDocument, Gap, NoAngle, UnrelatedDocument
from .prompts import DOC_TYPES, PROMPT_VERSION
from .refs import canonical_ref, display_ref, format_ref, parse_ref
from .runner import MAX_GROUP_CHARS, angle_mapper_applies_to_item, run_angle_mapper, run_message_angle_map
from .store import load_angle_map_for_item, load_fresh_angle_map_for_item, write_angle_map
from .validate import UNPLACED_WHY, unplaced_core

__all__ = [
    "Angle",
    "AngleMap",
    "AngleMapRunResult",
    "Client",
    "ClientInDocument",
    "DOC_TYPES",
    "Gap",
    "LABEL_AR",
    "MAX_GROUP_CHARS",
    "NoAngle",
    "PROMPT_VERSION",
    "UNPLACED_WHY",
    "UnrelatedDocument",
    "angle_mapper_applies_to_item",
    "angle_viewpoint",
    "canonical_ref",
    "display_ref",
    "format_ref",
    "load_angle_map_for_item",
    "load_fresh_angle_map_for_item",
    "parse_ref",
    "relative_label",
    "resolve_ref",
    "run_angle_mapper",
    "run_message_angle_map",
    "section_index",
    "section_side",
    "unplaced_core",
    "write_angle_map",
]
