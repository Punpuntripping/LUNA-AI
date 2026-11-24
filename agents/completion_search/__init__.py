"""Completion Search — the lookup helper other agents call for a NAMED legal text.

Plan: ``.claude/plans/completion_search.md``. A caller sends plain words; the helper
returns the official wording with a confidence level. Word match only, no search fee,
never talks to the user.
"""
from agents.completion_search.lookups import Library, SupabaseLibrary
from agents.completion_search.models import CompletionResult, Pin, SearchOutcome
from agents.completion_search.runner import render_for_caller, run_completion_search

__all__ = [
    "CompletionResult",
    "Library",
    "Pin",
    "SearchOutcome",
    "SupabaseLibrary",
    "render_for_caller",
    "run_completion_search",
]
