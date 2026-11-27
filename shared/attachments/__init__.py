"""User-requested edits to uploaded files (attachment agent, plan
``.claude/plans/attachment_agent.md`` §3).

Two overlays that hang on the FILE (``user_files.file_id``) and never rewrite
its text or its segment map (migration 234):

- :mod:`.text_corrections` — small extraction fixes (``wrong`` → ``right``),
  applied to the text by every reader that opts in (D12, D14, D15);
- :mod:`.segment_overrides` — sections the user promoted to ``core``, applied
  inside ``attachment_segmenter.store.load_segment_maps`` so every reader of a
  section's ``value`` sees them (§3.1).

Angle-map edits live with the angle map: ``agents/memory/angle_mapper/edits.py``.

Leaf modules: they import ``shared`` only (``backend`` lazily, for decoding), so
``agents`` and ``backend`` can both use them without an import cycle.
"""
