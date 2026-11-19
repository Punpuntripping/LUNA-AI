"""Files in their own table (``user_files``) — .claude/plans/my_files_phase2.md §C.

``flags`` decides whether a reader / writer takes the file path; ``store`` holds
the only code that reads or writes ``user_files``. Leaf modules: they import
``shared.config`` and nothing from ``backend`` or ``agents``, so both can use
them without an import cycle.
"""
