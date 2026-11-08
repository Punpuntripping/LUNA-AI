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
    #: ``read_full_text`` is on for this run (ARTIFACT_ANALYZER_FULL_TEXT_ENABLED).
    full_text: bool = False
    #: label → row. Together with ``opened`` this is the only text a quote may
    #: come from, which is what makes quotes checkable.
    reports: dict[str, ReportRow] = field(default_factory=dict)
    sources: dict[str, SourceRow] = field(default_factory=dict)
    #: label → whole text of every source the model opened this run.
    opened: dict[str, str] = field(default_factory=dict)
    #: Labels taken by ``read_full_text`` this run, in order. A label is taken
    #: before the read starts, so two calls in one round cannot pass the limit.
    full_read: list[str] = field(default_factory=list)
    #: label → the full half ``read_full_text`` returned, exactly as the model saw
    #: it. Quotable, like ``opened``; «ما قرأه البحث» is not.
    full_texts: dict[str, str] = field(default_factory=dict)

    def source_for_citation(self, report_label: str, n: int) -> SourceRow | None:
        for source in self.sources.values():
            if (report_label, n) in source.cited_in:
                return source
        return None
