"""item_analyzer — Layer-4 Memory librarian (analyze flow).

Single public entrypoint: ``analyze(AnalyzerCall, AnalyzerDeps) -> AnalyzeOutput``.
The runner partitions ``targeted_wi`` into refs vs meta families and fires at
most two LLM calls (one per family). See ``.claude/plans/item_analyzer_v2.md``
for the full design.

The companion editor agent (``item_analyzer_editor``) is the only agent
allowed to mutate ``workspace_items.content_md`` after the initial insert;
it ships in v2.1.
"""
from .deps import AnalyzerDeps, CallerId, build_analyzer_deps  # noqa: F401
from .models import (  # noqa: F401
    AnalyzeOutput,
    AnalyzerCall,
    AnalyzerError,
    MetaAnalyzeOutput,
    MetaVerdict,
    MetaVerdictFull,
    MetaVerdictNone,
    MetaVerdictPartial,
    RefsAnalyzeOutput,
    RefsVerdict,
    RefsVerdictFull,
    RefsVerdictNone,
    RefsVerdictPartial,
    WIVerdict,
    WorkspaceItemRow,
)
from .runner import analyze  # noqa: F401

__all__ = [
    "AnalyzerDeps",
    "CallerId",
    "build_analyzer_deps",
    "AnalyzerCall",
    "AnalyzerError",
    "AnalyzeOutput",
    "WIVerdict",
    "RefsVerdict",
    "RefsVerdictFull",
    "RefsVerdictPartial",
    "RefsVerdictNone",
    "RefsAnalyzeOutput",
    "MetaVerdict",
    "MetaVerdictFull",
    "MetaVerdictPartial",
    "MetaVerdictNone",
    "MetaAnalyzeOutput",
    "WorkspaceItemRow",
    "analyze",
]
