"""artifact_editor — Layer-3 surgical editor for one workspace artifact.

Invoked by the router's ``edit_artifact`` tool
(``agents/tool_repository/edit_artifact.py``). See ``agent.py`` and
``.claude/plans/artifact_editor.md``.
"""
from agents.artifact_editor.agent import (
    EditorDeps,
    EditorResult,
    run_artifact_editor,
)

__all__ = ["run_artifact_editor", "EditorResult", "EditorDeps"]
