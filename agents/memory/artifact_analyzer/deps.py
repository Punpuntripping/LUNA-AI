"""Per-run state of one analyzer call."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ReportRow, SourceRow


@dataclass
class AnalyzerDeps:
    supabase: Any
    user_id: str
    conversation_id: str
    case_id: str | None = None
    #: label → row. Together with ``opened`` this is the only text a quote may
    #: come from, which is what makes quotes checkable.
    reports: dict[str, ReportRow] = field(default_factory=dict)
    sources: dict[str, SourceRow] = field(default_factory=dict)
    #: label → whole text of every source the model opened this run.
    opened: dict[str, str] = field(default_factory=dict)

    def source_for_citation(self, report_label: str, n: int) -> SourceRow | None:
        for source in self.sources.values():
            if (report_label, n) in source.cited_in:
                return source
        return None
