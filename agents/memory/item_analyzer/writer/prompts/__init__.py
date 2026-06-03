"""Prompts for the writer-planner caller of item_analyzer.

Two modules, one per output family:

- ``refs_kinds`` — refs-family prompt + renderer (kinds: agent_search, agent_writing).
  These WIs carry ``[n]`` reference tokens in their ``content_md``.
- ``meta_kinds`` — meta-family prompt + renderer (kinds: attachment, notes).
  Prose / OCR-extracted text; no inline ref tokens.

Each module exports a module-level ``ANALYZE_*_FOR_WRITER_SYSTEM_AR`` string
plus a ``render_*_user_msg(*, query, wis) -> str`` pure function. Both are
imported by ``item_analyzer.prompt_registry``.
"""
