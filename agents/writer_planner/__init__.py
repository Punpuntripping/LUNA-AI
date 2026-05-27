"""writer_planner — the conversational planner phase in front of writing_executor.

Layer-2 Major agent that examines what the user provided, optionally calls
``item_analyzer`` for context distillation, optionally searches the system
template library, presents a plan to the user (when strategy is unclear),
iterates on feedback, and finally hands a ``WriterPackage`` to the
writing_executor.

Public surface:
    handle_writer_planner_turn / WriterPlannerTurnResult   (runner.py)
    create_writer_planner_decider / WRITER_PLANNER_LIMITS  (agent.py)
    WriterPlannerDeps + build_writer_planner_deps          (deps.py)
    PlannerDecision                                        (models.py)

See `.claude/plans/writer_planner.md` for the architectural spec.
"""
from __future__ import annotations

from .agent import WRITER_PLANNER_LIMITS, create_writer_planner_decider
from .deps import WriterPlannerDeps, build_writer_planner_deps
from .models import EditMode, PlannerDecision, PlannerRole
from .runner import WriterPlannerTurnResult, handle_writer_planner_turn

__all__ = [
    "WRITER_PLANNER_LIMITS",
    "create_writer_planner_decider",
    "WriterPlannerDeps",
    "build_writer_planner_deps",
    "PlannerDecision",
    "PlannerRole",
    "EditMode",
    "WriterPlannerTurnResult",
    "handle_writer_planner_turn",
]
