"""style_profiler — Layer-4 memory agent (tier_2 flash) that distils HOW a user
writes (register, preferred length, counsel vs party, verbatim legal terms)
into an identifier-free profile stored in ``user_style_profiles`` (migration
176) and rendered into the unified responder's dynamic tail.

Plan: ``.claude/plans/unified_responder.md`` §7 (phase P4).
"""
from __future__ import annotations

from .agent import STYLE_PROFILER_LIMITS, create_style_profiler
from .models import StyleProfile, StyleProfilerInput, looks_like_identifier
from .prompts import render_profile_md
from .runner import (
    load_style_profile_md,
    maybe_refresh_style_profile,
    run_style_profiler,
    schedule_style_profile_refresh,
    should_refresh,
)

__all__ = [
    "STYLE_PROFILER_LIMITS",
    "StyleProfile",
    "StyleProfilerInput",
    "create_style_profiler",
    "load_style_profile_md",
    "looks_like_identifier",
    "maybe_refresh_style_profile",
    "render_profile_md",
    "run_style_profiler",
    "schedule_style_profile_refresh",
    "should_refresh",
]
