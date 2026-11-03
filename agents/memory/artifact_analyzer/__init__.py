"""artifact_analyzer — Layer-4 Memory (plan artifact_analyzer.md).

Decides whether what a conversation already holds answers the user's message,
with verbatim quotes, before another deep search runs. Silent: it never talks to
the user. Single entry point: ``analyze(AnalyzerCall, supabase) -> AnalyzerResult``.
"""
from .models import (
    AnalyzerCall,
    AnalyzerHandoff,
    AnalyzerResult,
    CoveredQuote,
    VerifiedQuote,
)
from .runner import analyze

__all__ = [
    "AnalyzerCall",
    "AnalyzerHandoff",
    "AnalyzerResult",
    "CoveredQuote",
    "VerifiedQuote",
    "analyze",
]
