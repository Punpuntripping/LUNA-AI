"""Pydantic models + deps for the deep-search v4 Planner agent (cut-1.5).

The planner's decision surface is intentionally narrow:

- :attr:`PlannerOutput.invoke` — which executors to run.
- :attr:`PlannerOutput.focus`  — per-invoked-executor focus level (``high`` /
  ``default`` / ``low``). Mapped programmatically in
  :mod:`agents.deep_search_v4.planner.apply` to concrete expander +
  reranker numbers via ``FOCUS_PROFILES``.
- :attr:`PlannerOutput.sectors` — pre-filter; promoted out of reg's expander.
- :attr:`PlannerOutput.rationale` — short Arabic justification (logged).

Aggregator prompt key, expander caps, reranker caps, and RRF thresholds are
**not** chosen by the LLM — they're derived from ``invoke`` + ``focus`` in code,
so the planner stays cheap and the tuning surface stays out of the prompt.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator


Executor = Literal["reg", "compliance", "cases"]
FocusLevel = Literal["high", "default", "low"]


class PlannerOutput(BaseModel):
    """Lightweight plan emitted by a single planner LLM call.

    The schema is deliberately small: 4 fields, no nested caps/thresholds. The
    apply step (:mod:`.apply`) expands ``focus`` into concrete numbers and
    derives the aggregator prompt key from ``invoke``.
    """

    invoke: list[Executor] = Field(
        ...,
        description=(
            "Subset of {'reg', 'compliance', 'cases'}. At least one executor "
            "must be chosen. Order is irrelevant — the apply step uses a set."
        ),
    )
    focus: dict[Executor, FocusLevel] = Field(
        ...,
        description=(
            "Per-invoked-executor focus level. Must contain a key for every "
            "entry in ``invoke``. Disabled executors should be omitted."
        ),
    )
    sectors: list[str] | None = Field(
        default=None,
        description=(
            "1–4 canonical sector names from "
            "``agents.deep_search_v4.reg_search.sector_vocab.VALID_SECTORS``, "
            "or ``null`` when the query isn't sector-specific."
        ),
    )
    rationale: str = Field(
        ...,
        description="Short Arabic justification for invoke + focus choices.",
    )
    aborted: bool = Field(
        default=False,
        description=(
            "Set True when the user's response after an ask_user deferral is "
            "so off-script that the agent cannot continue.  The orchestrator "
            "should re-route via the router instead of resuming the current "
            "planner run."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("invoke")
    @classmethod
    def _validate_invoke_nonempty_unique(cls, value: list[Executor]) -> list[Executor]:
        if not value:
            raise ValueError("invoke must contain at least one executor")
        seen: set[str] = set()
        for e in value:
            if e in seen:
                raise ValueError(f"invoke contains duplicate executor: {e!r}")
            seen.add(e)
        return value

    @field_validator("focus")
    @classmethod
    def _validate_focus_keys(
        cls, value: dict[Executor, FocusLevel], info,
    ) -> dict[Executor, FocusLevel]:
        # ``invoke`` is validated first (declaration order). Cross-field check.
        invoked = info.data.get("invoke") or []
        invoked_set = set(invoked)
        focus_set = set(value.keys())
        missing = invoked_set - focus_set
        if missing:
            raise ValueError(
                f"focus must include every invoked executor; missing: {sorted(missing)}"
            )
        # Extra keys are tolerated but stripped to keep apply deterministic.
        return {k: v for k, v in value.items() if k in invoked_set}

    @field_validator("sectors", mode="before")
    @classmethod
    def _coerce_sectors(cls, v):
        # Coerce LLM quirks before strict list[str] validation:
        # - JSON-stringified arrays: '["x","y"]' -> ["x","y"]
        # - empty string: ''  -> None
        if isinstance(v, str):
            if v.strip() == "":
                return None
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return v

    @field_validator("sectors")
    @classmethod
    def _validate_sectors_size(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        # Fallback rule: ≥5 entries means the query is too broad — drop the filter.
        if len(value) >= 5:
            return None
        if len(value) < 1:
            raise ValueError(
                f"sectors must contain 1–4 entries or be null; got {len(value)}"
            )
        return value


@dataclass
class PlannerDeps:
    """Runtime deps for :func:`agents.deep_search_v4.planner.runner.run_planner`.

    Intentionally minimal — the planner is a single LLM call with no corpus
    access. ``_events`` mirrors the orchestrator's per-phase SSE buffer; if
    ``emit_sse`` is provided the runner also calls it on every event.
    """

    model_override: str | None = None
    _events: list[dict] = field(default_factory=list)
    emit_sse: Callable[[dict], None] | None = None


__all__ = ["Executor", "FocusLevel", "PlannerOutput", "PlannerDeps"]
