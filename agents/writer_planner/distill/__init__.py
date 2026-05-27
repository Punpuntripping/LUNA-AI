"""writer_planner.distill — non-LLM retrieval helpers used by the planner.

Only one helper for v1: ``search_templates`` (pgvector cosine over
``system_templates``). The historical batch-distillation gating logic has
been removed — distillation is now owned by the shared item_analyzer
(.claude/plans/item_analyzer_v2.md). See ``template_search.py``.
"""
from __future__ import annotations

from .template_search import search_templates

__all__ = ["search_templates"]
